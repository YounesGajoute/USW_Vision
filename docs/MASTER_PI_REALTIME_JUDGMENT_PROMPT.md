# Cursor agent prompt — Master Pi: Real-time judgment feedback

Copy everything inside the block below into a new Cursor chat on the **US Machine (master RPI)** repo (`~/US Machine` or your master project path).

Do **not** re-implement this on the vision slave Pi (`inspection_vision`) — the slave already has it in Configure Step 2. Your job is to wire the same behavior into **Settings → Vision → Tool configuration** (and optionally **General template**) on the master.

---

## Task: Real-time judgment on Tool Configuration (Master Pi)

### Context

The **master RPI** has no CSI camera. It proxies capture, master image, and live frames from the **vision slave** via `VISION_URL`. Tool configuration is drawn on a **640×480 wizard canvas**; ROIs are stored in wizard pixels (`toolsRoiSpace: wizard_640x480`).

The vision slave has **no tool-preview API** for instant threshold feedback. Judgment runs **entirely in the browser** on images you already have (master base64 + optional live frame). Production pass/fail still comes from **Save program** / **Run once** on the slave inspection pipeline.

Reference implementation (copy from `inspection_vision`):

| File | Purpose |
|------|---------|
| `inspection_vision/lib/toolJudgment.ts` | Fast ROI metrics (master + live, template-aware) |
| `inspection_vision/components/wizard/RealTimeJudgmentStrip.tsx` | UI strip below threshold slider |
| `inspection_vision/master-ui-reference/lib/toolJudgment.ts` | Same (portable copy) |
| `inspection_vision/master-ui-reference/components/RealTimeJudgmentStrip.tsx` | Same (portable copy) |

Slave wizard wiring reference: `inspection_vision/components/wizard/Step3ToolConfiguration.tsx` (search `analyzeToolJudgment`, `RealTimeJudgmentStrip`, `judgmentWithPipeline`).

---

### What “Real-time judgment” must do

1. **Placement**: A **Real-time judgment** strip **below the threshold slider** in `ToolEditPanel` / `VisionToolsEditor` (or equivalent).
2. **80ms debounce** when ROI, tool type, master image, or live frame changes → recompute metrics.
3. **Instant PASS/FAIL** when only the threshold slider moves (compare cached score to limit; no re-analysis).
4. **Display** per channel (master + live):
   - Metric label + score (e.g. `Edge strength · 72`)
   - Green **PASS** / red **FAIL** badge (`score >= threshold`, or `threshold ≤ score ≤ upperLimit` if upper limit set)
   - Threshold meter bar, margin vs limit
   - Optional detail line (edge fill %, bright area %, etc.)
5. **Empty states**:
   - No master image → “Load or register a master image…”
   - No ROI → “Draw a ROI on the master canvas…”
6. **Suggested threshold**: button “Use N%” from master signal (~8pt below current fast score).
7. **Pipeline overlay (optional)**: If you later add client-side `previewDetectionToolMatch` (same as slave `lib/inspection-engine.ts`), merge match % into snapshot via `mergePipelineScores`. Until then, fast scores alone are acceptable.
8. **Canvas feedback (recommended)**: Green/red outer glow on the active tuning/insight ROI when PASS/FAIL changes (see slave `drawInspectionRoiDecoration` + `judgmentToneRef`).

---

### Files to add on Master Pi

Copy into your frontend (adjust `@/` imports to your aliases):

```
frontend/src/lib/toolJudgment.ts          ← from inspection_vision
frontend/src/components/vision/RealTimeJudgmentStrip.tsx   ← from inspection_vision
```

**Types required** (align with slave or define locally):

```ts
type ToolType = 'outline' | 'area' | 'color_area' | 'edge_detection' | 'position_adjust';

interface ROI {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface ToolConfig {
  id: string;
  type: ToolType;
  name: string;
  color: string;
  roi: ROI;
  threshold: number;
  upperLimit?: number;
}
```

**UI dependencies** (match your design system or adapt strip to your components):

- `Badge`, `Button` (shadcn or equivalent)

---

### Integration in `ToolEditPanel` / `VisionToolsEditor`

#### 1. State

```ts
const [judgmentSnapshot, setJudgmentSnapshot] = useState<ToolJudgmentSnapshot | null>(null);
const [judgmentBusy, setJudgmentBusy] = useState(false);
const judgmentSeq = useRef(0);
```

#### 2. Resolve active judgment target (wizard ROI + tool type)

When user is drawing/editing ROI, tuning threshold only, or viewing a saved tool:

```ts
const judgmentTarget = useMemo(() => {
  // tuningTool = tool in threshold-only mode
  // insightTool = saved tool selected for feedback when idle
  // currentRect = ROI being drawn/edited (640×480)
  // Return null for position_adjust or when no ROI
  return { toolType, roi, toolId?: string } | null;
}, [/* currentRect, selectedToolType, tuningToolId, insightToolId, configuredTools, editMode */]);
```

#### 3. Debounced analysis effect

```ts
useEffect(() => {
  if (!judgmentTarget || !imageB64) {  // imageB64 = master from canvas/parent
    setJudgmentSnapshot(null);
    setJudgmentBusy(false);
    return;
  }

  setJudgmentBusy(true);
  const seq = ++judgmentSeq.current;
  const timer = window.setTimeout(async () => {
    try {
      const result = await analyzeToolJudgment(
        imageB64,
        judgmentTarget.toolType,
        judgmentTarget.roi,
        {
          roiInWizardSpace: true,
          toolId: judgmentTarget.toolId,
          masterFeatures: masterFeaturesState,  // optional Record from extractMasterFeatures
          liveImageBase64: liveFrameB64,        // optional 640×480 live from WS proxy
        }
      );
      if (seq === judgmentSeq.current) setJudgmentSnapshot(result);
    } catch {
      if (seq === judgmentSeq.current) setJudgmentSnapshot(null);
    } finally {
      if (seq === judgmentSeq.current) setJudgmentBusy(false);
    }
  }, 80);

  return () => window.clearTimeout(timer);
}, [judgmentTarget, imageB64, masterFeaturesState, liveFrameB64]);
```

**`imageB64`**: Full-resolution master from `GET /api/vision/master-image/{programId}` or parent state — **not** only the scaled canvas bitmap if you can avoid it (judgment scales wizard ROI → native pixels internally).

**`liveFrameB64`**: Latest live frame normalized to **640×480** (same as slave `imageBase64ToWizardFrame640`) from your `useVisionLiveFeed` hook.

#### 4. Optional pipeline merge

If you run full preview match (client `previewDetectionToolMatch` or slave proxy):

```ts
const judgmentWithPipeline = useMemo(
  () => mergePipelineScores(judgmentSnapshot, {
    master: pipelineMasterMatchRate,
    live: pipelineLiveMatchRate,
  }),
  [judgmentSnapshot, pipelineMasterMatchRate, pipelineLiveMatchRate]
);
```

Otherwise pass `judgmentSnapshot` directly to the strip.

#### 5. Render strip below slider

```tsx
<Slider value={threshold} onValueChange={setThreshold} min={0} max={100} step={1} />

{panelToolType !== 'position_adjust' && (
  <RealTimeJudgmentStrip
    snapshot={judgmentWithPipeline ?? judgmentSnapshot}
    threshold={threshold}
    upperLimit={activeTool?.upperLimit}
    busy={judgmentBusy}
    hasMasterImage={!!imageB64}
    hasJudgmentTarget={!!judgmentTarget}
    livePaused={livePaused}
    hasLiveFrame={!!liveFrameB64}
    onApplySuggestedThreshold={(v) => setThreshold(v)}
  />
)}
```

#### 6. Pass `imageB64` from parent

`VisionToolsEditor` (or tab container) must pass:

```tsx
<ToolEditPanel
  imageB64={masterImageBase64}
  liveFrameB64={wizardLiveFrame640}
  masterFeaturesState={masterFeatures}
  ...
/>
```

---

### Metric mapping (browser-only)

| Tool type | Fast metric | Notes |
|-----------|-------------|--------|
| `outline`, `edge_detection` | Edge strength | Sobel strong-edge %; with master features → template edge-density match |
| `area` | Area signal | Otsu bright-area % vs template `brightAreaRatio` |
| `color_area` | Color variation | RGB spread in ROI |
| `position_adjust` | *(none)* | Hide judgment strip |

**PASS rule**: `displayScore(channel) >= threshold` (and `<= upperLimit` if set).  
**Instant slider**: Only re-run `judgmentPass(displayScore(...), threshold)` — do not re-call `analyzeToolJudgment` on slider move.

---

### Master features (optional but recommended)

After master image + tools load, extract features once (port `extractMasterFeatures` from slave `lib/inspection-engine.ts` or call slave if you add a proxy). Pass `masterFeaturesState` into `analyzeToolJudgment` so fast scores align with saved template geometry.

Without features, judgment still works using raw edge/contrast metrics.

---

### Live feed on Master Pi

- Proxy Socket.IO `live_frame` from vision slave (see `master-ui-reference/hooks/useVisionLiveFeed.ts`).
- Decode to 640×480 for judgment + canvas overlay.
- When live paused or unavailable, live card shows “Resume camera…” / “Live frame not available yet.”

---

### What NOT to do

- Do **not** add a new slave API `POST /tool-preview` unless product explicitly requires server-side preview on master.
- Do **not** store judgment scores in program config — only **threshold** and **ROI** persist.
- Do **not** use judgment PASS/FAIL as production inspection result; label UI: “Browser-side estimate… Save & run once for production results.”

---

### Acceptance checklist

- [ ] Copy `toolJudgment.ts` + `RealTimeJudgmentStrip.tsx`; fix imports.
- [ ] Strip appears below threshold slider on Tool configuration tab.
- [ ] With master loaded + ROI drawn: shows metric, score, PASS/FAIL.
- [ ] Moving slider updates PASS/FAIL **without** delay; moving ROI updates score after ~80ms.
- [ ] Master and Live cards both populate when live feed active.
- [ ] “Use N%” sets threshold from `suggestThreshold`.
- [ ] `position_adjust` tool: no judgment strip.
- [ ] Save program tools → `PUT /api/vision/programs/{id}` unchanged; judgment is UI-only.
- [ ] Build passes; no TypeScript errors on new files.

---

### Reference: key exports from `toolJudgment.ts`

```ts
analyzeToolJudgment(masterB64, toolType, roi, options?) → ToolJudgmentSnapshot | null
mergePipelineScores(snapshot, { master?, live? }) → ToolJudgmentSnapshot | null
displayScore(channel) → number | null
judgmentPass(score, threshold, upper?) → boolean | null
suggestThreshold(masterChannel) → number | null
wizardRoiToImagePixels(roi, imgW, imgH) → ROI
TOOL_JUDGMENT_DEBOUNCE_MS // 80
```

---

### Related docs on vision slave repo

- `inspection_vision/docs/MASTER_SETTINGS_VISION_UI_PROMPT.md` — full Vision Settings tabs
- `inspection_vision/docs/MASTER_VISION_CONNECTIVITY.md` — `VISION_URL`, remote key
- `inspection_vision/master-ui-reference/README.md` — file list for porting

---

*End of prompt — paste into Master Pi Cursor chat.*
