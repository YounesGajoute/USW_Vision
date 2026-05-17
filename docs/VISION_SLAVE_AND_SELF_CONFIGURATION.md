# Vision Inspection System: Self-Control vs Ethernet Slave

This guide explains how to run the system in two modes:

- **Self-controlled** — operators use the Next.js UI (and local REST) on the same machine or browser as usual.
- **Ethernet slave** — a **master** Raspberry Pi or PC drives programs, triggers inspections, and receives results over the network; the vision Pi runs camera, GPIO, and inspection logic only.

---

## 1. Common baseline (both modes)

1. **Hardware** — CSI camera (e.g. IMX296), optional GPIO outputs, optional P9813 lighting (`config.yaml` → `lighting`).
2. **Backend** — `backend/` with `config.yaml` + `app.py`, or production `app_production.py` + environment variables.
3. **Programs** — SQLite; `POST/PUT /api/programs` or the Configure UI.

**REST base URL**

| Deployment        | Typical REST prefix                 |
|-------------------|-------------------------------------|
| Dev (`app.py`)    | `http://<vision-ip>:5000/api`      |
| Production        | `http://<vision-ip>:5000/api/v1`   |

**Socket.IO** — same host and port as HTTP, path `/socket.io/`.

### 1.1 Fixed LAN address (static IP)

Stable URLs (`http://<vision-ip>:5000`, `NEXT_PUBLIC_WS_URL`, master `VISION_URL`) need an address that does not change after reboot. See **[MASTER_VISION_CONNECTIVITY.md](./MASTER_VISION_CONNECTIVITY.md)** when the master cannot ping the vision Pi.

**Option A — DHCP reservation (recommended)**  
On your router, reserve the Pi’s MAC address to always receive the same IPv4 (e.g. `192.168.1.20`). No change on the Pi; you only use that IP in `config.yaml`, env vars, and bookmarks.

**Option B — Static IPv4 on the Pi (Raspberry Pi OS, NetworkManager)**  
Current Raspberry Pi OS images usually use **NetworkManager**. On the vision Pi:

1. List connections and note the name for Ethernet (often `Wired connection 1` or `preconfigured`):

   ```bash
   nmcli connection show
   ```

2. Set a manual address (replace names and numbers for your LAN):

   ```bash
   sudo nmcli connection modify "Wired connection 1" \
     ipv4.method manual \
     ipv4.addresses 192.168.1.20/24 \
     ipv4.gateway 192.168.1.1 \
     ipv4.dns "192.168.1.1,1.1.1.1"
   sudo nmcli connection up "Wired connection 1"
   ```

   - **`ipv4.addresses`** — `IP/prefix` (e.g. `/24` for `255.255.255.0`).
   - **`ipv4.gateway`** — your router’s LAN IP.
   - **`ipv4.dns`** — comma-separated DNS servers (router + public is fine).

3. Confirm and persist after reboot:

   ```bash
   ip -4 addr show
   ping -c 2 192.168.1.1
   ```

**Avoid clashes** — Pick an IP outside the router’s DHCP pool, or shrink the pool so your static IP is excluded.

**Optional GUI** — `sudo nmtui` → *Edit a connection* → IPv4 → *Manual* → address, gateway, DNS → *OK* → activate the connection.

**Legacy (dhcpcd)** — Older images may use `/etc/dhcpcd.conf` with `interface eth0`, `static ip_address=…`, `static routers=…`, `static domain_name_servers=…`. Prefer NetworkManager on current OS; see [Raspberry Pi documentation — IP address](https://www.raspberrypi.com/documentation/computers/configuration.html#ip-address).

### 1.2 Start UI + API at boot (systemd)

The repo ships **`scripts/inspection-vision.service`**, which runs **`npm run start:all`** (Next.js production + Flask) as user **`bot`** from **`/home/bot/inspection_vision`**.

- **Install / enable on boot:**  
  `sudo ./scripts/install_boot_service.sh`  
  then `sudo systemctl start inspection-vision` (or reboot).

- **Logs / status:**  
  `sudo journalctl -u inspection-vision -f` · `sudo systemctl status inspection-vision`

Requires a built frontend (`npm run build` in the project root) so `next start` can serve.

---

## Quick start: vision Pi as Ethernet slave

Do this on the **vision Pi** (the one with the camera). Operators or a **master** Pi/PC then call REST / Socket.IO on `http://<vision-ip>:5000`.

1. **Network** — Use §1.1: fixed IP via **DHCP reservation** or **manual IPv4** on the Pi. Ensure the master can reach port **5000** (and firewall allows it).

2. **Edit `backend/config.yaml`**
   - **`api.cors_origins`** — Add every `Origin` that will load Socket.IO: master dashboard URLs (`http://<master-ip>:3000`), any operator browser, etc.
   - **`slave`** (production line):
     - `require_real_hardware: true` — fail fast if camera/GPIO will not start; no fake test pattern.
     - `require_remote_api_key: true` — forces you to set a non-empty `remote.api_key` (or env).
   - **`remote`**:
     - `api_key: "<long random secret>"` — master sends `X-Vision-Remote-Key` or `Authorization: Bearer …` on `POST .../remote/inspection/run-once`.
     - `socketio_auth: inherit` — same secret on Socket.IO connect as `auth: { remoteKey: "<same>" }` (matches `vision_master_client.py` when `VISION_REMOTE_KEY` is set).
     - Keep `socketio_cors: "*"` only on a **trusted LAN**; narrow if you can.

3. **Optional env overrides** (same meaning as YAML): `VISION_REMOTE_API_KEY`, `VISION_SOCKETIO_AUTH_MODE`, `VISION_SOCKETIO_AUTH_KEY` — see §3.3.

4. **Start backend** — `app.py` (dev) or `app_production.py` with the env table in §3.4 if you use production mode.

5. **Verify from the master (or any PC on the LAN)**

   ```bash
   curl -s "http://<vision-ip>:5000/api/remote/info" | python3 -m json.tool
   ```

   If `api_key` is set, protected calls need the header:

   ```bash
   curl -s -H "X-Vision-Remote-Key: your-secret" \
     "http://<vision-ip>:5000/api/remote/info" | python3 -m json.tool
   ```

6. **Master (US Machine)** — In master `backend/.env` set `VISION_URL=http://<vision-ip>:5000` (no `/api`) and `VISION_REMOTE_KEY`. Then:

   ```bash
   unset VISION_SLAVE_URL
   pip install -r scripts/requirements-master-client.txt
   ./scripts/vision-master.sh check
   ./scripts/vision-master.sh run-once <program_id>
   ```

   Do **not** use `http://127.0.0.1:5000` from the master — the API runs on the vision Pi only.

Details, production env names, and Next.js keys are in §3–§6 below.

---

## 2. Self-controlled operation

### 2.1 Network (`config.yaml`)

```yaml
api:
  host: "0.0.0.0"
  port: 5000
  cors_origins:
    - "http://localhost:3000"
    - "http://<operator-pc-ip>:3000"
```

List every browser `Origin` that loads the Next app.

### 2.2 Live preview / inspection

- **REST** — your existing proxy / env.
- **Socket.IO** — `NEXT_PUBLIC_WS_URL=http://<vision-ip>:5000` (see `lib/websocket.ts`).

### 2.3 Development conveniences

- **`camera.allow_test_pattern: true`** (default when `slave.require_real_hardware` is false) — if CSI fails, a synthetic pattern is used so the UI still shows *something*. **Not for production inspection.**

---

## 3. Ethernet slave (development `app.py` + `config.yaml`)

### 3.1 Production-style slave flags (`config.yaml` → `slave`)

| Key | Effect |
|-----|--------|
| **`require_real_hardware: true`** | Process **exits** if camera/GPIO init throws. If the camera never opens, **no test-pattern images**: captures return errors; `POST /remote/.../run-once` returns **503**; Socket.IO `start_inspection` / `subscribe_live_feed` emit **`NO_CAMERA`**; live stream **stops** on capture failure instead of faking frames. |
| **`require_remote_api_key: true`** | `remote.api_key` (or `VISION_REMOTE_API_KEY`) **must** be non-empty; otherwise `run-once` returns **503** (misconfiguration). |

**Default `camera.allow_test_pattern`:** if `slave.require_real_hardware` is **true** and you do **not** set `camera.allow_test_pattern`, it defaults to **false**. You can still force `allow_test_pattern: true` for debugging (not recommended on a real line).

### 3.2 Remote REST and Socket.IO auth (`config.yaml` → `remote`)

| `remote.api_key` | `POST /api/remote/inspection/run-once` |
|------------------|----------------------------------------|
| Empty | Open (LAN trust only). |
| Set | Requires `X-Vision-Remote-Key` or `Authorization: Bearer …`. |

**`remote.socketio_auth`**

| Value | Behaviour |
|-------|-----------|
| **`none`** | No check on Socket.IO `connect` (default). |
| **`inherit`** | When `api_key` is set, clients must pass **`auth: { remoteKey: "<same as api_key>" }`** on connect (plus CORS as usual). |
| **`secondary`** | Uses **`remote.socketio_key`** or env **`VISION_SOCKETIO_AUTH_KEY`** only for Socket.IO. |

Override mode with env **`VISION_SOCKETIO_AUTH_MODE`** (`none` | `inherit` | `secondary`) on the vision Pi.

**`GET /api/remote/info`** returns `socketio_connect_auth_required`, `require_real_hardware`, `require_remote_api_key_configured`, and hints for the master.

### 3.3 Environment overrides (same semantics as YAML)

| Variable | Purpose |
|----------|---------|
| `VISION_REMOTE_API_KEY` | Shared secret for REST `run-once` (and `inherit` Socket.IO auth). |
| `VISION_SOCKETIO_AUTH_MODE` | `none` / `inherit` / `secondary`. |
| `VISION_SOCKETIO_AUTH_KEY` | Secret when mode is `secondary`. |
| `VISION_SOCKETIO_CORS` | Production app only: Socket.IO CORS (e.g. `*`). |

### 3.4 Production app (`app_production.py`)

No `config.yaml` slave block — use:

| Variable | Purpose |
|----------|---------|
| `VISION_SLAVE_REQUIRE_HARDWARE=1` | Same as `slave.require_real_hardware`. |
| `VISION_SLAVE_REQUIRE_REMOTE_API_KEY=1` | Same as `slave.require_remote_api_key`. |
| `VISION_ALLOW_CAMERA_TEST_PATTERN=0` or `1` | Override test pattern (default: **off** when hardware required, **on** otherwise). |

Socket.IO auth: set **`VISION_SOCKETIO_AUTH_MODE=inherit`** when using `VISION_REMOTE_API_KEY`, so masters must send `auth.remoteKey` on connect.

### 3.5 Master client

```bash
# Master backend/.env:
#   VISION_URL=http://192.168.10.2:5000
#   VISION_REMOTE_KEY=your-secret

unset VISION_SLAVE_URL
pip install -r scripts/requirements-master-client.txt
./scripts/vision-master.sh check
./scripts/vision-master.sh run-once 11
./scripts/vision-master.sh socket 11 --fps 12
```

The script sends **`auth: { remoteKey }`** on Socket.IO when `--key` / `VISION_REMOTE_KEY` is set.

### 3.6 Next.js + secured Socket.IO

- REST: `api.visionRemoteKey` on `APIClient` (see `lib/api.ts`).
- Socket.IO: set **`NEXT_PUBLIC_VISION_SOCKETIO_KEY`** to the same secret as the slave’s Socket.IO auth (only on trusted LANs — the value is **visible in the browser bundle**).

---

## 4. What runs where (master vs slave)

| Task | Mechanism |
|------|-----------|
| Discovery | `GET .../remote/info` |
| Programs CRUD | `GET/POST/PUT/DELETE .../programs` |
| One-shot inspection + image | `POST .../remote/inspection/run-once` |
| Continuous inspection | Socket.IO `start_inspection` |
| Live stream | Socket.IO `subscribe_live_feed` |
| Stop | `stop_inspection`, `unsubscribe_live_feed` |

---

## 5. Lighting (P9813) and GPIO

- **`gpio.outputs`** — BCM for OUT1–OUT8; keep **separate** from P9813 CLK/DATA.
- **`lighting.p9813`** — enable on the vision Pi for stable illumination.

---

## 6. Files and env summary

| Item | Role |
|------|------|
| `backend/config.yaml` | `api`, `slave`, `remote`, `camera`, `lighting`, `gpio` |
| `backend/app.py` | Loads YAML slave/remote settings |
| `backend/app_production.py` | `VISION_*` env for slave + Socket.IO |
| `scripts/vision_master_client.py` | Master CLI |
| `lib/api.ts`, `lib/websocket.ts` | Browser client + optional `NEXT_PUBLIC_VISION_SOCKETIO_KEY` |

---

## 7. Security

- Treat **`remote.api_key`** like a password; prefer TLS (reverse proxy) off-island.
- **`socketio_cors: "*"`** — trusted LAN only; narrow origins when possible.
- Firewall so only the master and operator subnets reach the API port.
- Avoid **`NEXT_PUBLIC_VISION_SOCKETIO_KEY`** on untrusted networks (secret is public to anyone who can load the site).

---

*Document matches: remote routes, optional hardware enforcement, Socket.IO connect auth, P9813 lighting, IMX296 stack.*
