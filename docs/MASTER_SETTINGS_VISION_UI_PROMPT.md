# Cursor agent prompt — US Machine (master RPI)

Copy everything inside the block below into a new Cursor chat on the **US Machine** repo (`~/US Machine`).  
Do **not** implement this on the vision slave Pi (`inspection_vision`); the slave already has its own local Configure wizard.

---

## Task: Settings → Vision UI (master controls vision slave remotely)

### Context

The **master RPI** (US Machine) does not have the CSI camera. All capture, master registration, and tool templates are executed on the **vision slave** via HTTP API (`VISION_URL`). The master UI must **proxy or call** those endpoints and render results on a canvas.

Reference UI (match layout and controls):

1. **Master image tab** — large live/still canvas; overlay `Live WxH` / `FPS` / latency; status line `~N fps · N ms · WxH`; bottom row: **Capture** (primary black), **Load File** (outline), **Register** (grey until image ready).
2. **Tool configuration tab** — master-image canvas with ROI drawing; section **SELECT TOOL TYPE** — five horizontal cards: Outline Tool (blue), Area Tool (green), Color Area Tool (orange), Edge Detection (red), Position Adjustment (purple); each shows tool count badge.
3. **General template tab** — same canvas + SELECT TOOL TYPE + drawing; for **shared** tool templates (not program-owned).

Navigation structure:

```
Settings
  └── Vision
        ├── Master image        (sub-tab)
        ├── Tool configuration  (sub-tab)
        └── General template    (sub-tab)
```

### Prerequisites

1. Master `backend/.env`:

   ```env
   VISION_URL=http://<vision-pi-ip>:5000
   VISION_REMOTE_KEY="<same as vision VISION_REMOTE_API_KEY>"
   ```

2. Network: master can `ping` vision Pi and `curl $VISION_URL/api/remote/info` with key (see `inspection_vision/docs/MASTER_VISION_CONNECTIVITY.md`).

3. Reuse or copy from `inspection_vision` if missing on US Machine:
   - `scripts/vision_master_client.py`
   - `scripts/vision-master.sh`
   - `scripts/requirements-master-client.txt`
   - `scripts/examples/tool-template.example.json`

### Architecture (master side)

| Layer | Responsibility |
|-------|----------------|
| **Settings UI** | Tabs, program selector, canvas, tool picker, buttons |
| **Master API routes** | Proxy to `$VISION_URL/api/*` with correct auth headers |
| **Vision slave** | Camera, disk storage, inspection engine (unchanged) |

Do **not** duplicate slave wizard components (`Step2MasterImage`, `Step3ToolConfiguration`) into the slave repo from the master; implement master-specific React (or existing US Machine stack) that calls master backend proxies.

### Program selector (shared across tabs)

- Dropdown of programs from `GET /api/programs?active_only=true` (proxied).
- Required for **Master image** (register) and **Tool configuration** (save tools to program / owned template).
- Optional for **General template** (shared templates only).

### Tab 1 — Master image

**UI**

- Canvas ~960×540 (or responsive) showing:
  - **Live view**: poll or stream from slave (prefer `Socket.IO live_frame` to slave if master proxies WS; otherwise periodic `POST /api/camera/capture` at modest rate, e.g. 2–4 fps max to avoid hammering the Pi).
  - **Still view**: after Capture or Load File, show captured base64 image.
- Top-left overlay on live: `Live {resolution}`, `FPS {n}  {latency} ms`.
- Below canvas: pulsing dot + `~{fps} fps · {ms} ms · {resolution} (full-res PNG)`.
- Buttons: **Capture**, **Load File**, **Register** (disabled until image present; show Registered state when done).

**API flow (via master proxy → vision slave)**

| Action | Vision slave endpoint | Notes |
|--------|----------------------|--------|
| Capture | `POST /api/camera/capture` | Body: `{ brightnessMode, exposureTime, analogGain, digitalGain }` → `{ image: base64, width, height, quality }` |
| Register | `POST /api/master-image` | `multipart/form-data`: `programId`, `file` (PNG/JPEG from capture) |
| Load existing | `GET /api/master-image/{programId}` | `{ image: base64 }` for canvas when program selected |

**Register** must upload to the selected program on the **slave**, not only store state in the master UI.

### Tab 2 — Tool configuration

**UI**

- Same master image on canvas (from `GET /api/master-image/{programId}` or last capture).
- **SELECT TOOL TYPE** row (five tools, counts, selection highlight) — match slave Configure step 2 styling.
- Interactive ROI drawing on **640×480 wizard space** (scale display canvas from master image; store ROIs in wizard pixels).
- Toolbar: grid, legend, master vs live backdrop if live preview is available.
- List configured tools; threshold tuning; **Save** persists to program.

**Data rules**

- ROIs: **wizard canvas 640×480** (`toolsRoiSpace: wizard_640x480`) — same as slave Configure UI.
- Tool types: `outline`, `area`, `color_area`, `edge_detection`, `position_adjust` (max 16 tools, max 1 position_adjust).
- Save program tools: `PUT /api/programs/{id}` with `config.tools` (and existing trigger/outputs).
- Program-owned template: on save, slave may auto-maintain `toolTemplateId` for that program name (if slave supports it); otherwise `POST /api/tool-templates` with program-specific name.

**Apply template**

- `GET /api/tool-templates` — list; filter owned vs shared as needed.
- `GET /api/tool-templates/{id}/for-program/{programId}` — ROIs scaled to master resolution for editing.

### Tab 3 — General template

**UI**

- Same as Tool configuration: master canvas + SELECT TOOL TYPE + ROI drawing.
- Focus on **shared** templates (any program can apply later).
- **Save as template** → `POST /api/tool-templates` with `{ name, description?, tools }` — **no** reference image stored.
- **Apply template** → load tools onto current canvas/master; user may pick target program when applying for run.

Difference from Tab 2:

| | Tool configuration | General template |
|--|-------------------|------------------|
| Scope | Tied to selected program | Shared across programs |
| Save target | Program config + optional owned template | `POST /tool-templates` only |
| programId | Required | Not required to author |

### Vision API reference (base = `$VISION_URL/api`)

| Purpose | Method | Path | Auth |
|---------|--------|------|------|
| Health | GET | `/remote/info` | `X-Vision-Remote-Key` |
| Programs | GET | `/programs` | LAN / local key |
| Update program | PUT | `/programs/{id}` | LAN / local key |
| Capture | POST | `/camera/capture` | LAN / local key |
| Register master | POST | `/master-image` | multipart: `programId`, `file` |
| Get master | GET | `/master-image/{programId}` | LAN |
| Templates list | GET | `/tool-templates` | LAN |
| Template CRUD | GET/POST/DELETE | `/tool-templates/{id}` | LAN |
| Template for program | GET | `/tool-templates/{id}/for-program/{programId}` | LAN |
| Run inspection | POST | `/remote/inspection/run-once` | `X-Vision-Remote-Key` |
| Run with template | POST | `/inspection/run-with-template` | LAN |

Headers:

- `X-Vision-Remote-Key`: remote routes on slave.
- `X-Vision-Local-Key`: only if slave has `VISION_LOCAL_API_KEY` set.

### Tool JSON shape (wizard ROI space)

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

Example pack: `inspection_vision/scripts/examples/tool-template.example.json`.

### Master backend proxy (suggested routes)

Add under US Machine backend, e.g. `/api/vision/...`:

- `GET /api/vision/programs`
- `POST /api/vision/camera/capture`
- `POST /api/vision/master-image` (forward multipart + programId)
- `GET /api/vision/master-image/:programId`
- `GET|POST|DELETE /api/vision/tool-templates[...]`
- `PUT /api/vision/programs/:id` (config.tools)
- Optional: WebSocket proxy for `live_frame` events

Load `VISION_URL` and `VISION_REMOTE_KEY` from master `backend/.env` only (never commit secrets).

### Live preview guidance

- **Avoid** 1 Hz full `POST /camera/capture` polling on the slave (loads IMX296 every second). Prefer:
  - Socket.IO subscription to slave `live_frame` through a master WS proxy, or
  - Lower-rate capture (2–4 s) for master settings only, or
  - Static image until user clicks Capture.

### Success criteria

- [ ] Settings → Vision appears in US Machine UI with three sub-tabs.
- [ ] Master image: Capture and Register save master to selected program on slave (`GET master-image` returns image).
- [ ] Tool configuration: draw ROIs, save updates program tools on slave; run-once uses new ROIs.
- [ ] General template: save shared template; apply to a program with registered master; `run-with-template` works.
- [ ] No changes required on vision slave `inspection_vision` frontend for this feature.

### Do not

- Implement Settings → Vision on the vision slave Pi frontend.
- Use `VISION_SLAVE_URL=http://127.0.0.1:5000` on the master.
- Assume master has a local CSI camera.

### Related docs (vision Pi repo)

- `inspection_vision/docs/MASTER_VISION_CONNECTIVITY.md`
- `inspection_vision/docs/MASTER_AGENT_PROMPT.md` (CLI / API smoke test)
- `inspection_vision/docs/VISION_SLAVE_AND_SELF_CONFIGURATION.md`

---
