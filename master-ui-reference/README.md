# Master Pi — Vision Settings UI (reference)

Corrected **Settings → Vision → Master image** implementation for the **US Machine (master RPI)**. Copy these files into the master repo and map `@/` imports to your project aliases.

The vision **slave** (`inspection_vision`) exposes:

| Action | Method | Path |
|--------|--------|------|
| Capture | POST | `/api/camera/capture` |
| Register | POST | `/api/master-image` (multipart: `programId`, `file`) |
| Load | GET | `/api/master-image/{programId}` → `{ image, format }` |
| Live | Socket.IO | `live_frame` on same host as API |

The master backend should proxy these under e.g. `/api/vision/...` (see `docs/MASTER_SETTINGS_VISION_UI_PROMPT.md`).

## Corrections vs. the original `MasterImageTab`

1. **Explicit `isRegistered` flag** — no longer inferred by comparing base64 strings (re-encode on the slave would break that).
2. **PNG/JPEG mime tracking** — register uploads use the correct `File` type and extension (capture is lossless PNG).
3. **Reload after register** — `GET /master-image/{id}` loads the canonical on-disk image from the vision Pi.
4. **Resume live** — clears the local still/draft but keeps the registered master in `onMasterImageChange` when already saved.
5. **10 MB file limit** — matches vision slave `validate_file_upload(max_size_mb=10)`.
6. **Live stats row** — FPS / latency / resolution under the canvas (matches reference screenshots).
7. **`extractImageB64`** — tolerates `{ image }`, data-URI prefixes, and common proxy wrapper shapes.

## Files

- `lib/visionWizard.ts` — base64 helpers, mime detection
- `services/visionService.ts` — master API client (adjust `VISION_API_PREFIX` if needed)
- `hooks/useVisionLiveFeed.ts` — Socket.IO live frames via master WS proxy
- `components/VisionImageCanvas.tsx` — canvas + overlays
- `components/MasterImageTab.tsx` — main tab UI
