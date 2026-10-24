# AeroDestructionSim Dedicated Multiplayer Server (Render.com)

Сервер мультиплеера для авиасимулятора Aero Destruction Simulator, готовый к мгновенному развертыванию на бесплатном тарифе Render.com.

---

## 🚀 Инструкция по развертыванию на Render (2 минуты):

1. **Войдите в Render.com**:
   - В вашем браузере уже открыта страница [https://dashboard.render.com](https://dashboard.render.com).
   - Нажмите **"Sign in with Google"** (войдите через свой Google-аккаунт).

2. **Создайте новый Web Service**:
   - Нажмите синюю кнопку **"New +"** в правом верхнем углу.
   - Выберите **"Web Service"**.

3. **Подключите репозиторий или загрузите код**:
   - **Вариант А (через GitHub/GitLab - Рекомендуется)**:
     - Создайте публичный или приватный репозиторий на GitHub (например, `aero-destruction-server`).
     - Загрузите содержимое папки `server/` в этот репозиторий.
     - В Render выберите ваш репозиторий и нажмите **Connect**.
   - **Вариант Б (Render Blueprint)**:
     - Render автоматически считает файл `render.yaml` и настроит всё без лишних кликов!

4. **Параметры сервиса**:
   - **Name**: `aero-destruction-sim` (или любое желаемое имя)
   - **Region**: Frankfurt (EU) или Oregon (US)
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn server:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: `Free` (бесплатный тариф)

5. **Нажмите "Deploy Web Service"**:
   - Через 30-60 секунд сервер запустится!
   - Render выдаст вам постоянный публичный адрес вида:
     `https://aero-destruction-sim.onrender.com`
   - WebSocket URL для игры будет:
     `wss://aero-destruction-sim.onrender.com/ws`

---

## 💻 Локальный запуск (для проверки на своем ПК):

В терминале перейдите в папку `server` и выполните:
```bash
python server.py
```
Сервер будет доступен локально по адресу `ws://localhost:8080/ws`.
