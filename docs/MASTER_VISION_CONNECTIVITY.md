# Master ↔ Vision connectivity

Application code and remote auth can be correct while HTTP still fails: the **master must reach the vision Pi at Layer 3**. This is the checklist used when `vision-master.sh run-once 11` fails from the US Machine.

## Symptom map (from the master)

| Error | Typical cause |
|-------|----------------|
| `127.0.0.1:5000` connection refused | `VISION_SLAVE_URL` or default pointed at localhost; vision API is **not** on the master |
| `192.168.10.2` no route to host / ping destination unreachable | Vision Pi is **not** on `192.168.10.x` (e.g. only on WiFi `192.168.100.x`) |
| TCP open but HTTP 401/403 | `VISION_REMOTE_KEY` ≠ vision `VISION_REMOTE_API_KEY` |
| HTTP 503 on run-once | Camera/hardware or `require_remote_api_key` misconfiguration on vision Pi |

## Verified layout (example)

| Machine | Role | Example IP | Notes |
|---------|------|------------|--------|
| US Machine | Master | `192.168.10.1/24` | `backend/.env` → `VISION_URL` |
| vision Pi | Slave | **Target** for `VISION_URL` | Flask `0.0.0.0:5000`, key in `backend/.env` |

If the vision Pi only has WiFi `192.168.100.121` and the master only has `192.168.10.1`, **they cannot talk** until you fix routing or put both on one reachable subnet.

## Fix options (pick one)

### Option A — Recommended (production LAN)

Put the vision Pi on **Ethernet** at `192.168.10.2/24`, gateway `192.168.10.1`, same switch/VLAN as the master.

On the **vision Pi**:

```bash
sudo ./scripts/configure_vision_lan_eth.sh
# or manually: nmcli connection modify "Wired connection 1" ipv4.method manual ...
```

Plug in Ethernet (eth0 must show carrier UP). Verify on vision Pi:

```bash
curl -s http://127.0.0.1:5000/api/remote/info
```

On the **master** (`backend/.env` unchanged):

```env
VISION_URL=http://192.168.10.2:5000
VISION_REMOTE_KEY="Techmac@@Gajoute1992"
```

```bash
unset VISION_SLAVE_URL
ping -c 2 192.168.10.2
./scripts/vision-master.sh check
./scripts/vision-master.sh run-once 11
```

### Option B — WiFi IP (only if master can reach that subnet)

```env
VISION_URL=http://192.168.100.121:5000
```

Test from master: `ping -c 2 192.168.100.121` — if ping fails, fix WiFi/router first.

### Option C — Tailscale (debug or remote sites)

Vision Pi example: `100.110.239.22`

```env
VISION_URL=http://100.110.239.22:5000
```

Both nodes must be on the same tailnet; `ping` and `curl .../api/remote/info` from the master.

## Master configuration

| Variable | Where | Value |
|----------|--------|--------|
| `VISION_URL` | Master `backend/.env` | `http://<reachable-ip>:5000` — **no** `/api` suffix |
| `VISION_REMOTE_KEY` | Master `backend/.env` | Same as vision `VISION_REMOTE_API_KEY` |
| `VISION_SLAVE_URL` | Shell | **Unset** on master; do not use `http://127.0.0.1:5000/api` |

Vision Pi `backend/.env` holds `VISION_REMOTE_API_KEY` (not `VISION_URL`).

## Master checklist

```bash
unset VISION_SLAVE_URL
grep VISION ~/US\ Machine/backend/.env   # or your master repo path
ping -c 2 <VISION_IP>
curl -s -H "X-Vision-Remote-Key: $VISION_REMOTE_KEY" \
  "http://<VISION_IP>:5000/api/remote/info"
./scripts/vision-master.sh check
./scripts/vision-master.sh run-once 11
```

Use **program 11** (or another valid id from `./scripts/vision-master.sh programs`).

## Scripts

| Script | Runs on | Purpose |
|--------|---------|---------|
| `scripts/vision-master.sh` | Master | Load `.env`, unset bad `VISION_SLAVE_URL`, invoke client |
| `scripts/vision_master_client.py` | Master | REST/Socket.IO; resolves `VISION_URL` → `.../api` |
| `scripts/configure_vision_lan_eth.sh` | Vision Pi | Static `192.168.10.2` on wired (Option A) |
| `docs/MASTER_AGENT_PROMPT.md` | Master agent | Copy-paste task: master image + tool template + run-once |

Install master deps: `pip install -r scripts/requirements-master-client.txt`

## What is already OK when TCP fails

- Remote key match (`X-Vision-Remote-Key` / `VISION_REMOTE_API_KEY`)
- Vision service listening on `0.0.0.0:5000`
- `run-once` works on the vision Pi via `curl` to `127.0.0.1`

None of that substitutes for a routable IP between master and slave.
