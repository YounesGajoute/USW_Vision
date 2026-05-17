# Testing

**Frontend (Vitest):** from the repo root run `npm test`. Tests live under `lib/**/*.test.ts` (pure helpers such as `run-program` and `inspection-transport`).

**Backend (pytest):** run `npm run test:backend` or `cd backend && .venv/bin/pytest tests -q`. Tests do not require camera hardware.

**Manual Pi matrix:** after changes, verify on hardware: REST program run, REST template run, WebSocket live feed, optional `NEXT_PUBLIC_INSPECTION_TRANSPORT=ws` program inspection, and Stop/Pause clearing timers and WS threads.
