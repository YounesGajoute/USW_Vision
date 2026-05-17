# Cursor agent prompt — US Machine (master)

Copy everything inside the block below into a new Cursor chat on the **US Machine** repo (`~/US Machine`).

---

## Task: Vision slave — register master image + tool template + run inspection

### Prerequisites (network)

1. `backend/.env` on the master:

   ```env
   VISION_URL=http://192.168.10.2:5000
   VISION_REMOTE_KEY="Techmac@@Gajoute1992"
   ```

   Use the vision Pi IP the master can **ping** (see `inspection_vision/docs/MASTER_VISION_CONNECTIVITY.md`).

2. Shell:

   ```bash
   unset VISION_SLAVE_URL
   pip install -r scripts/requirements-master-client.txt
   ./scripts/vision-master.sh check
   ```

3. Copy from `inspection_vision` into US Machine if missing:
   - `scripts/vision_master_client.py`
   - `scripts/vision-master.sh`
   - `scripts/requirements-master-client.txt`

### Goal

From the **master**, against the **vision Pi** API:

1. **Register a master (reference) image** for program **11** (camera capture → disk on vision Pi).
2. **Create a tool configuration template** (ROIs + thresholds in wizard space 640×480).
3. **Verify** with `run-once 11` (requires program 11 to have tools in its config, or use template run — see below).

### Vision API reference (base = `$VISION_URL/api`)

| Step | Method | Path | Auth |
|------|--------|------|------|
| Discovery | GET | `/remote/info` | `X-Vision-Remote-Key` if slave requires it |
| List programs | GET | `/programs?active_only=true` | Usually open on LAN |
| Live capture | POST | `/camera/capture` | Body: `{"brightnessMode":"normal"}` → `{image: base64, quality, ...}` |
| Register master | POST | `/master-image` | `multipart/form-data`: `file`, `programId` |
| Get master | GET | `/master-image/<programId>` | Returns `{image: base64, format}` |
| Save template | POST | `/tool-templates` | JSON: `{name, tools, description?}` |
| List templates | GET | `/tool-templates` | |
| Run inspection | POST | `/remote/inspection/run-once` | JSON: `{programId, includeImage, triggerType:"remote"}` |
| Template + program | POST | `/inspection/run-with-template` | `{templateId, programId, includeImage}` |

- **`X-Vision-Remote-Key`**: required for `/api/remote/*` (matches vision `VISION_REMOTE_API_KEY`).
- **`/api/camera/capture`**, **`/api/master-image`**, **`/api/tool-templates`**: use LAN trust unless vision has `VISION_LOCAL_API_KEY` set (then send `X-Vision-Local-Key`).

### Tool template JSON rules

- ROIs are in **wizard canvas pixels** (640×480), same as the Configure UI.
- Each tool:

  ```json
  {
    "id": "area-1",
    "type": "area",
    "name": "Seal check",
    "color": "#3b82f6",
    "roi": { "x": 100, "y": 80, "width": 120, "height": 90 },
    "threshold": 85,
    "upperLimit": 100
  }
  ```

- Valid `type`: `outline`, `area`, `color_area`, `edge_detection`, `position_adjust` (max 1 position tool).
- Max 16 tools per program/template.
- Templates store **tools only** (no reference image). Apply template to a program that already has a **registered master image**.

Example file: `scripts/examples/tool-template.example.json` in `inspection_vision`.

### Implementation options

**A — Use the master CLI (preferred)**

```bash
cd ~/US\ Machine
unset VISION_SLAVE_URL

# 1) Register master image for program 11 (live CSI capture on vision Pi)
./scripts/vision-master.sh register-master 11

# 2) Create template from JSON (edit ROIs/thresholds first)
./scripts/vision-master.sh create-template "US Line master layout" \
  --tools scripts/examples/tool-template.example.json \
  --description "Master-side template"

# 3) List programs / templates
./scripts/vision-master.sh programs
curl -s "$VISION_URL/api/tool-templates" | python3 -m json.tool

# 4) Run inspection (program must include tools in DB config, or use run-with-template via curl)
./scripts/vision-master.sh run-once 11
```

**B — curl equivalents**

```bash
KEY='Techmac@@Gajoute1992'
BASE='http://192.168.10.2:5000/api'

# Capture
curl -s -X POST "$BASE/camera/capture" \
  -H 'Content-Type: application/json' \
  -d '{"brightnessMode":"normal"}' -o /tmp/cap.json
# Decode image field → /tmp/frame.png, then:
curl -s -X POST "$BASE/master-image" \
  -F "programId=11" -F "file=@/tmp/frame.png;type=image/png"

# Template
curl -s -X POST "$BASE/tool-templates" \
  -H 'Content-Type: application/json' \
  -d @scripts/examples/tool-template.example.json

# Inspect
curl -s -X POST "$BASE/remote/inspection/run-once" \
  -H "X-Vision-Remote-Key: $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"programId":11,"triggerType":"remote","includeImage":true}'
```

**C — Master UI / proxy**

If the US Machine backend already proxies `VISION_URL`, add or use endpoints that:

1. Proxy `POST /api/camera/capture` and `POST /api/master-image` for a given `programId`.
2. Proxy `POST /api/tool-templates` with tools JSON from the configure flow.
3. Proxy `POST /api/remote/inspection/run-once` or `POST /api/inspection/run-with-template`.

### Applying a template to program 11

`run-once` uses tools stored **on the program**. To run with a template without editing the program in the UI:

```bash
curl -s -X POST "$BASE/inspection/run-with-template" \
  -H 'Content-Type: application/json' \
  -d '{"templateId":1,"programId":11,"includeImage":true,"triggerType":"remote"}'
```

Or `PUT /api/programs/11` with `config.tools` copied from the template (scaled ROIs are applied server-side on template run; program save should use wizard ROIs).

### Success criteria

- `./scripts/vision-master.sh check` → HTTP 200 on `/remote/info`
- `./scripts/vision-master.sh register-master 11` → `{path, quality, message}`
- `./scripts/vision-master.sh create-template ...` → HTTP 201, `template.id`
- `./scripts/vision-master.sh run-once 11` → HTTP 200, `status` OK|NG, optional `image` base64

### Do not

- Use `VISION_SLAVE_URL=http://127.0.0.1:5000` on the master
- Assume build/restart fixes Layer-3 failures
- Store secrets in repo commits (use `backend/.env` only)

### Related docs (vision Pi repo)

- `docs/MASTER_VISION_CONNECTIVITY.md` — subnet / static IP / Tailscale
- `docs/VISION_SLAVE_AND_SELF_CONFIGURATION.md` — slave mode and auth

---
