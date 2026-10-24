import os
import time
import json
import asyncio
from typing import Dict, List, Optional
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
        self.room_code = "PUBLIC"
        self.is_host = False
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
            "alive": self.is_alive,
            "is_host": self.is_host,
            "room": self.room_code
        }

class Room:
    def __init__(self, code: str, host_id: str):
        self.code = code
        self.host_id = host_id
        self.players: Dict[str, PlayerConnection] = {}
        self.map_index = 0
        self.npcs: List[dict] = []
        self.created_at = time.time()

    def get_host(self) -> Optional[PlayerConnection]:
        return self.players.get(self.host_id)

    def reassign_host(self):
        if self.players:
            new_host_id = next(iter(self.players))
            self.host_id = new_host_id
            self.players[new_host_id].is_host = True
            return new_host_id
        return None

rooms: Dict[str, Room] = {}
players: Dict[str, PlayerConnection] = {}
next_player_id = 1
lock = asyncio.Lock()

@app.get("/")
async def root():
    return JSONResponse({
        "status": "online",
        "game": "Aero Destruction Simulator",
        "total_players": len(players),
        "active_rooms": len(rooms),
        "rooms": {
            rc: {
                "players": len(r.players),
                "host": r.players.get(r.host_id, PlayerConnection(None, "")).callsign if r.host_id in r.players else "None",
                "map_index": r.map_index,
                "npc_count": len(r.npcs)
            } for rc, r in rooms.items()
        },
        "server_time": time.time()
    })

@app.get("/healthz")
async def healthz():
    return {"status": "ok"}

async def broadcast_room(room_code: str, message: dict, exclude_id: str = None):
    room = rooms.get(room_code)
    if not room:
        return
    data_str = json.dumps(message)
    dead_ids = []
    for pid, p in list(room.players.items()):
        if exclude_id and pid == exclude_id:
            continue
        try:
            await p.ws.send_text(data_str)
        except Exception:
            dead_ids.append(pid)
    for did in dead_ids:
        if did in room.players:
            del room.players[did]
        if did in players:
            del players[did]
    if not room.players:
        if room_code in rooms and room_code != "PUBLIC":
            del rooms[room_code]

# 25 Hz room-scoped world state broadcast
async def broadcast_loop():
    while True:
        try:
            await asyncio.sleep(0.04) # ~25 FPS
            for rc, room in list(rooms.items()):
                if room.players:
                    packet = {
                        "type": "world_state",
                        "room": rc,
                        "t": time.time(),
                        "host_id": room.host_id,
                        "map_index": room.map_index,
                        "players": {pid: p.to_dict() for pid, p in room.players.items()},
                        "npcs": room.npcs
                    }
                    await broadcast_room(rc, packet)
        except asyncio.CancelledError:
            break
        except Exception:
            pass

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(broadcast_loop())

def get_or_create_room(code: str, creator_id: str) -> Room:
    code = code.strip().upper() or "PUBLIC"
    if code not in rooms:
        rooms[code] = Room(code, creator_id)
    return rooms[code]

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
        
        # Place initially in PUBLIC room
        initial_room = get_or_create_room("PUBLIC", pid)
        initial_room.players[pid] = player
        player.room_code = "PUBLIC"
        if initial_room.host_id == pid:
            player.is_host = True

    print(f"[CONNECT] Player {pid} joined (Room: PUBLIC)")

    try:
        # Welcome Handshake
        welcome_pkt = {
            "type": "welcome",
            "your_id": pid,
            "room_code": player.room_code,
            "is_host": player.is_host,
            "host_id": initial_room.host_id,
            "map_index": initial_room.map_index,
            "players": {pk: pv.to_dict() for pk, pv in initial_room.players.items() if pk != pid},
            "npcs": initial_room.npcs
        }
        await websocket.send_text(json.dumps(welcome_pkt))

        # Notify room members
        await broadcast_room(player.room_code, {
            "type": "player_joined",
            "player": player.to_dict()
        }, exclude_id=pid)

        # Main message loop
        while True:
            raw_text = await websocket.receive_text()
            try:
                msg = json.loads(raw_text)
            except Exception:
                continue

            msg_type = msg.get("type")
            player.last_seen = time.time()
            curr_room = rooms.get(player.room_code)

            if msg_type == "join_room":
                # Switch or Create Room
                target_code = str(msg.get("room_code", "PUBLIC")).strip().upper()[:12] or "PUBLIC"
                if target_code != player.room_code:
                    old_room_code = player.room_code
                    async with lock:
                        # Remove from old room
                        if curr_room and pid in curr_room.players:
                            del curr_room.players[pid]
                            if curr_room.host_id == pid:
                                new_host = curr_room.reassign_host()
                                await broadcast_room(old_room_code, {
                                    "type": "host_changed",
                                    "host_id": new_host
                                })
                            await broadcast_room(old_room_code, {
                                "type": "player_left",
                                "player_id": pid,
                                "callsign": player.callsign
                            })
                            if not curr_room.players and old_room_code != "PUBLIC":
                                del rooms[old_room_code]

                        # Join new room
                        new_room = get_or_create_room(target_code, pid)
                        new_room.players[pid] = player
                        player.room_code = target_code
                        player.is_host = (new_room.host_id == pid)
                        curr_room = new_room

                    # Send room joined confirmation
                    await websocket.send_text(json.dumps({
                        "type": "room_joined",
                        "room_code": target_code,
                        "is_host": player.is_host,
                        "host_id": new_room.host_id,
                        "map_index": new_room.map_index,
                        "players": {pk: pv.to_dict() for pk, pv in new_room.players.items() if pk != pid},
                        "npcs": new_room.npcs
                    }))

                    # Notify new room members
                    await broadcast_room(target_code, {
                        "type": "player_joined",
                        "player": player.to_dict()
                    }, exclude_id=pid)

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
                if "callsign" in msg:
                    player.callsign = str(msg["callsign"])[:16]

            elif msg_type == "npc_sync":
                # Host synchronizes NPC traffic positions & states
                if player.is_host and curr_room:
                    curr_room.npcs = msg.get("npcs", [])
                    await broadcast_room(player.room_code, {
                        "type": "npc_update",
                        "npcs": curr_room.npcs
                    }, exclude_id=pid)

            elif msg_type == "admin_cmd":
                # Host Admin Panel Commands
                if player.is_host and curr_room:
                    action = msg.get("action")
                    if action == "set_map":
                        new_map = int(msg.get("map_index", 0))
                        curr_room.map_index = new_map
                        await broadcast_room(player.room_code, {
                            "type": "admin_event",
                            "action": "set_map",
                            "map_index": new_map,
                            "by": player.callsign
                        })

                    elif action == "spawn_npc":
                        model = msg.get("model", "SHAHED")
                        count = int(msg.get("count", 1))
                        await broadcast_room(player.room_code, {
                            "type": "admin_event",
                            "action": "spawn_npc",
                            "model": model,
                            "count": count,
                            "by": player.callsign
                        })

                    elif action == "clear_npcs":
                        curr_room.npcs = []
                        await broadcast_room(player.room_code, {
                            "type": "admin_event",
                            "action": "clear_npcs",
                            "by": player.callsign
                        })

                    elif action == "rebuild_city":
                        await broadcast_room(player.room_code, {
                            "type": "admin_event",
                            "action": "rebuild_city",
                            "by": player.callsign
                        })

                    elif action == "sam_barrage":
                        await broadcast_room(player.room_code, {
                            "type": "admin_event",
                            "action": "sam_barrage",
                            "by": player.callsign
                        })

                    elif action == "kick_player":
                        target_id = msg.get("target_id")
                        if target_id and target_id in curr_room.players and target_id != pid:
                            kicked_p = curr_room.players[target_id]
                            try:
                                await kicked_p.ws.send_text(json.dumps({
                                    "type": "kicked",
                                    "reason": "Kicked by Room Host"
                                }))
                                await kicked_p.ws.close()
                            except Exception:
                                pass
                            await broadcast_room(player.room_code, {
                                "type": "kill_feed",
                                "killer_name": "HOST",
                                "victim_name": kicked_p.callsign,
                                "weapon": "KICKED"
                            })

            elif msg_type == "missile":
                await broadcast_room(player.room_code, {
                    "type": "remote_missile",
                    "shooter_id": pid,
                    "shooter_name": player.callsign,
                    "pos": msg.get("pos"),
                    "vel": msg.get("vel"),
                    "target_id": msg.get("target_id")
                }, exclude_id=pid)

            elif msg_type == "flare":
                await broadcast_room(player.room_code, {
                    "type": "remote_flare",
                    "shooter_id": pid,
                    "pos": msg.get("pos"),
                    "vel": msg.get("vel")
                }, exclude_id=pid)

            elif msg_type == "bomb":
                await broadcast_room(player.room_code, {
                    "type": "remote_bomb",
                    "shooter_id": pid,
                    "shooter_name": player.callsign,
                    "pos": msg.get("pos"),
                    "vel": msg.get("vel"),
                    "bomb_type": msg.get("bomb_type", "JDAM")
                }, exclude_id=pid)

            elif msg_type == "hit":
                target_id = msg.get("target_id")
                dmg = msg.get("damage", 25.0)
                if curr_room and target_id in curr_room.players:
                    tgt = curr_room.players[target_id]
                    tgt.health = max(0.0, tgt.health - dmg)
                    if tgt.health <= 0.0 and tgt.is_alive:
                        tgt.is_alive = False
                        await broadcast_room(player.room_code, {
                            "type": "kill_feed",
                            "killer_id": pid,
                            "killer_name": player.callsign,
                            "victim_id": target_id,
                            "victim_name": tgt.callsign,
                            "weapon": msg.get("weapon", "MISSILE")
                        })
                    else:
                        await broadcast_room(player.room_code, {
                            "type": "player_damaged",
                            "victim_id": target_id,
                            "damage": dmg,
                            "health": tgt.health
                        })

            elif msg_type == "chat":
                text = str(msg.get("text", "")).strip()[:100]
                if text:
                    await broadcast_room(player.room_code, {
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
            if curr_room and pid in curr_room.players:
                del curr_room.players[pid]
                if curr_room.host_id == pid:
                    new_host = curr_room.reassign_host()
                    if new_host:
                        await broadcast_room(player.room_code, {
                            "type": "host_changed",
                            "host_id": new_host
                        })
                await broadcast_room(player.room_code, {
                    "type": "player_left",
                    "player_id": pid,
                    "callsign": player.callsign
                })
                if not curr_room.players and player.room_code != "PUBLIC":
                    del rooms[player.room_code]
        print(f"[DISCONNECT] Player {pid} left {player.room_code}. Total: {len(players)}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    print(f"Starting AeroDestructionSim Room-Based Dedicated Server on 0.0.0.0:{port}...")
    uvicorn.run("server:app", host="0.0.0.0", port=port, log_level="info")
