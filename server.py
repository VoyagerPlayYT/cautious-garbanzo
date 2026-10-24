import os
import time
import json
import asyncio
from typing import Dict
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

app = FastAPI(title="AeroDestructionSim Multiplayer Server")

class PlayerConnection:
    def __init__(self, ws: WebSocket, player_id: str, callsign: str = "Pilot", model: str = "F16", color: list = None):
        self.ws = ws
        self.player_id = player_id
        self.callsign = callsign
        self.model = model
        self.color = color or [0.2, 0.6, 1.0]
        self.pos = [0.0, 100.0, 0.0]
        self.vel = [0.0, 0.0, 0.0]
        self.rot = [0.0, 0.0, 0.0] # pitch, yaw, roll
        self.speed = 0.0
        self.throttle = 0.0
        self.gear = True
        self.flaps = 0
        self.health = 100.0
        self.is_alive = True
        self.last_seen = time.time()

    def to_dict(self):
        return {
            "id": self.player_id,
            "callsign": self.callsign,
            "model": self.model,
            "color": self.color,
            "pos": self.pos,
            "vel": self.vel,
            "rot": self.rot,
            "speed": self.speed,
            "throttle": self.throttle,
            "gear": self.gear,
            "flaps": self.flaps,
            "health": self.health,
            "alive": self.is_alive
        }

players: Dict[str, PlayerConnection] = {}
next_player_id = 1
lock = asyncio.Lock()

@app.get("/")
async def root():
    """Health check endpoint for Render.com automated monitors."""
    return JSONResponse({
        "status": "online",
        "game": "Aero Destruction Simulator",
        "connected_players": len(players),
        "server_time": time.time()
    })

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

async def broadcast(message: dict, exclude_id: str = None):
    data_str = json.dumps(message)
    dead_ids = []
    for pid, p in list(players.items()):
        if exclude_id and pid == exclude_id:
            continue
        try:
            await p.ws.send_text(data_str)
        except Exception:
            dead_ids.append(pid)
    for did in dead_ids:
        if did in players:
            del players[did]

# Periodic 25 Hz world state broadcast
async def broadcast_loop():
    while True:
        try:
            await asyncio.sleep(0.04) # ~25 FPS sync tick
            if players:
                state_packet = {
                    "type": "world_state",
                    "t": time.time(),
                    "players": {pid: p.to_dict() for pid, p in players.items()}
                }
                await broadcast(state_packet)
        except asyncio.CancelledError:
            break
        except Exception as e:
            pass

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(broadcast_loop())

@app.websocket("/ws")
@app.websocket("/")
async def websocket_endpoint(websocket: WebSocket):
    global next_player_id
    await websocket.accept()
    
    async with lock:
        pid = f"P_{next_player_id}"
        next_player_id += 1
        player = PlayerConnection(websocket, pid)
        players[pid] = player

    print(f"[CONNECT] Player {pid} connected from {websocket.client}")

    # Send Welcome packet with current world roster
    try:
        welcome_pkt = {
            "type": "welcome",
            "your_id": pid,
            "players": {p_k: p_v.to_dict() for p_k, p_v in players.items() if p_k != pid}
        }
        await websocket.send_text(json.dumps(welcome_pkt))

        # Notify other players
        await broadcast({
            "type": "player_joined",
            "player": player.to_dict()
        }, exclude_id=pid)

        # Message loop
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except Exception:
                continue

            msg_type = msg.get("type")
            player.last_seen = time.time()

            if msg_type == "join":
                player.callsign = msg.get("callsign", player.callsign)[:16]
                player.model = msg.get("model", player.model)
                player.color = msg.get("color", player.color)
                await broadcast({
                    "type": "player_info_updated",
                    "player": player.to_dict()
                })

            elif msg_type == "telemetry":
                player.pos = msg.get("pos", player.pos)
                player.vel = msg.get("vel", player.vel)
                player.rot = msg.get("rot", player.rot)
                player.speed = msg.get("speed", player.speed)
                player.throttle = msg.get("throttle", player.throttle)
                player.gear = msg.get("gear", player.gear)
                player.flaps = msg.get("flaps", player.flaps)
                player.health = msg.get("health", player.health)
                player.is_alive = msg.get("alive", True)
                if "model" in msg:
                    player.model = msg["model"]

            elif msg_type == "missile":
                # Forward missile spawn to all other players
                await broadcast({
                    "type": "remote_missile",
                    "shooter_id": pid,
                    "shooter_name": player.callsign,
                    "pos": msg.get("pos"),
                    "vel": msg.get("vel"),
                    "target_id": msg.get("target_id")
                }, exclude_id=pid)

            elif msg_type == "flare":
                # Forward flare deployment to all other players
                await broadcast({
                    "type": "remote_flare",
                    "shooter_id": pid,
                    "pos": msg.get("pos"),
                    "vel": msg.get("vel")
                }, exclude_id=pid)

            elif msg_type == "bomb":
                # Forward bomb release
                await broadcast({
                    "type": "remote_bomb",
                    "shooter_id": pid,
                    "shooter_name": player.callsign,
                    "pos": msg.get("pos"),
                    "vel": msg.get("vel"),
                    "bomb_type": msg.get("bomb_type", "JDAM")
                }, exclude_id=pid)

            elif msg_type == "hit":
                # Damage reported on another player
                target_id = msg.get("target_id")
                dmg = msg.get("damage", 25.0)
                if target_id in players:
                    tgt = players[target_id]
                    tgt.health = max(0.0, tgt.health - dmg)
                    if tgt.health <= 0.0 and tgt.is_alive:
                        tgt.is_alive = False
                        await broadcast({
                            "type": "kill_feed",
                            "killer_id": pid,
                            "killer_name": player.callsign,
                            "victim_id": target_id,
                            "victim_name": tgt.callsign,
                            "weapon": msg.get("weapon", "MISSILE")
                        })
                    else:
                        await broadcast({
                            "type": "player_damaged",
                            "victim_id": target_id,
                            "damage": dmg,
                            "health": tgt.health
                        })

            elif msg_type == "chat":
                text = str(msg.get("text", "")).strip()[:100]
                if text:
                    await broadcast({
                        "type": "chat",
                        "sender_id": pid,
                        "sender_name": player.callsign,
                        "text": text,
                        "time": time.time()
                    })

            elif msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong", "time": time.time()}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS ERROR] {pid}: {e}")
    finally:
        async with lock:
            if pid in players:
                del players[pid]
        print(f"[DISCONNECT] Player {pid} left. Remaining: {len(players)}")
        await broadcast({
            "type": "player_left",
            "player_id": pid,
            "callsign": player.callsign
        })

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting AeroDestructionSim Dedicated Server on 0.0.0.0:{port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, log_level="info")
