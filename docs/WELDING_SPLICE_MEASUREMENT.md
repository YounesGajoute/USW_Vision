# Welding splice and heat-shrink sleeve measurement

This guide describes how to capture images, measure **Welding splice** and **Heat-shrink sleeve** dimensions, and run regression tests on the inspection vision Pi.

All commands are run from the **repository root** (`inspection_vision/`).

## Prerequisites

- Python 3 with OpenCV: `pip install opencv-python-headless numpy` (and `pytest` for tests).
- A **calibration session** folder containing `calibration.json` (pixel scale). Example:

  `backend/storage/Calibration/session_20260517_081705/`

- For **live capture** (optional):
  - **API capture** (default): vision backend running; `VISION_URL` or `--api-url`.
  - **Local camera**: `--local-camera` and Picamera2 stack configured in `backend/config.yaml`.

- For full **heat-shrink sleeve** detection on assembly images, build the sleeve reference once (from golden captures `capture5.png` / `capture8.png`):

  ```bash
  python3 scripts/build_sleeve_reference.py
  ```

  Output: `backend/storage/reference/sleeve/sleeve_reference.json`

## Terminology and measurements

| Component | JSON key | Typical scene |
|-----------|----------|----------------|
| **Welding splice** | `welding_splice` | Copper joint between wire bundles |
| **Heat-shrink sleeve** | `heat_shrink_sleeve` | Matte black tube on the assembly |

| Symbol | Meaning |
|--------|---------|
| **L** | Length along the component main axis (wire / tube direction), in mm |
| **H** | Cross-section perpendicular to that axis (welding splice thickness or sleeve OD), in mm |

Scale comes from `calibration.json` (`px_per_mm`) or `--px-per-mm` override (default fallback ~5.1 px/mm).

---

## 1. Capture + measure assembly (welding splice + sleeve)

**Script:** `scripts/capture_measure_welding_splice_sleeve.py`

Captures one frame (or uses `--image`), measures both components, prints results, and writes a full artifact set under `backend/storage/Measurement/session_YYYYMMDD_HHMMSS/`.

### Recommended command

```bash
python3 scripts/capture_measure_welding_splice_sleeve.py \
  --calibration backend/storage/Calibration/session_20260517_081705
```

### What it does

1. Acquires an image (API camera, local camera, or `--image`).
2. Runs `measure_image()` in `measure_welding_splice_sleeve.py`.
3. Uses the welding splice contour as an **axis anchor** for heat-shrink sleeve detection when the splice is visible.
4. Saves masks, annotated image, and JSON.

### Output files

| File | Description |
|------|-------------|
| `capture.png` | Raw capture |
| `capture_measured.png` | Annotated image (L/H lines, labels) |
| `capture_mask_overlay.png` | Semi-transparent component masks |
| `capture_mask_welding_splice.png` | Welding splice mask (binary) |
| `capture_mask_sleeve.png` | Heat-shrink sleeve mask (binary) |
| `capture_mask_combined.png` | Both masks (BGR) |
| `capture_measurement.json` | Measurements, debug, scale, errors |

### Useful options

```bash
# Measure an existing file (no camera)
python3 scripts/capture_measure_welding_splice_sleeve.py \
  --image backend/storage/Measurement/session_20260517_130458/capture.png \
  --calibration backend/storage/Calibration/session_20260517_081705

# Custom output folder
python3 scripts/capture_measure_welding_splice_sleeve.py \
  -o backend/storage/Measurement/my_session \
  --calibration backend/storage/Calibration/session_20260517_081705

# Local Picamera2 instead of API
python3 scripts/capture_measure_welding_splice_sleeve.py \
  --local-camera \
  --calibration backend/storage/Calibration/session_20260517_081705

# Add a note in JSON
python3 scripts/capture_measure_welding_splice_sleeve.py \
  --calibration backend/storage/Calibration/session_20260517_081705 \
  --note "assembly check line 2"
```

| Option | Description |
|--------|-------------|
| `--image PATH` | Skip capture; measure this PNG |
| `-o`, `--output-dir` | Output directory (default: auto `session_*`) |
| `-c`, `--calibration` | Folder with `calibration.json` |
| `--px-per-mm` | Override scale |
| `--local-camera` | Capture via Picamera2 |
| `--api-url` | Vision API base URL |
| `--brightness-mode` | `normal`, `hdr`, or `highgain` |
| `--note` | Stored in measurement JSON |

### Example console output

```
Output: .../backend/storage/Measurement/session_20260517_143022

/path/to/capture.png
  scale: 5.100 px/mm (calibration.json)
  cable band y: 399–1088
  Welding splice: L=16.56 mm  H=9.41 mm  (angle …°)
  Heat-shrink sleeve: L=66.27 mm  H=12.48 mm  (angle …°)
```

---

## 2. Capture + measure welding splice only

**Script:** `scripts/capture_measure_welding_splice.py`

Same capture options as above, but only detects and measures the **welding splice** (no sleeve masks except overlay if present).

### Command

```bash
python3 scripts/capture_measure_welding_splice.py \
  --calibration backend/storage/Calibration/session_20260517_081705
```

### Output files

| File | Description |
|------|-------------|
| `capture.png` | Raw capture |
| `capture_measured.png` | Annotated welding splice |
| `capture_mask_overlay.png` | Mask overlay |
| `capture_mask_welding_splice.png` | Welding splice mask |
| `capture_measurement.json` | JSON (`welding_splice` only in measurement block) |

Use when tuning copper / welding splice HSV thresholds (`_copper_mask`, `_welding_splice_barrel_mask` in `measure_welding_splice_sleeve.py`).

---

## 3. Measure existing images (no capture)

**Script:** `scripts/measure_welding_splice_sleeve.py`

Batch-measure one or more PNG files; print results to the terminal. Optional annotated images, mask export, or combined JSON.

### Basic usage

```bash
python3 scripts/measure_welding_splice_sleeve.py \
  path/to/capture.png \
  --calibration backend/storage/Calibration/session_20260517_081705
```

### Multiple images and globs

```bash
python3 scripts/measure_welding_splice_sleeve.py \
  backend/storage/Measurement/session_*/capture.png \
  --calibration backend/storage/Calibration/session_20260517_081705
```

### Export artifacts per image

```bash
python3 scripts/measure_welding_splice_sleeve.py \
  backend/storage/Measurement/session_20260517_130458/capture.png \
  --calibration backend/storage/Calibration/session_20260517_081705 \
  --mask-dir /tmp/masks
```

Writes under `/tmp/masks/capture/` the same mask/measured files as the capture script.

### Annotated output

```bash
python3 scripts/measure_welding_splice_sleeve.py \
  path/to/capture.png \
  --calibration backend/storage/Calibration/session_20260517_081705 \
  --annotate-out /tmp/capture_measured.png
```

### Combined JSON for many images

```bash
python3 scripts/measure_welding_splice_sleeve.py \
  backend/storage/image_history/12/*.png \
  --calibration backend/storage/Calibration/session_20260517_081705 \
  --json-out /tmp/results.json
```

| Option | Description |
|--------|-------------|
| `images` | One or more paths (shell globs supported) |
| `-c`, `--calibration` | Calibration session folder |
| `--px-per-mm` | Override scale |
| `--annotate-out` | Single annotated PNG (one input only) |
| `--annotate-dir` | Annotated PNG per input stem |
| `--mask-dir` | Full mask/JSON artifact tree per image |
| `--json-out` | Aggregate results JSON |

### `capture_measurement.json` structure (summary)

```json
{
  "image_path": "...",
  "px_per_mm": 5.1,
  "welding_splice": {
    "name": "welding_splice",
    "found": true,
    "length_mm": 16.5,
    "height_mm": 9.4,
    "debug": { ... }
  },
  "heat_shrink_sleeve": {
    "name": "heat_shrink_sleeve",
    "found": true,
    "length_mm": 66.3,
    "height_mm": 12.5,
    "debug": { ... }
  },
  "errors": []
}
```

---

## 4. Run regression tests

**File:** `backend/tests/test_measure_welding_splice_sleeve.py`

Validates welding splice and sleeve dimensions on stored golden captures (skips tests if sample files are missing).

### Command

From the repository root:

```bash
pytest backend/tests/test_measure_welding_splice_sleeve.py
```

Verbose:

```bash
pytest backend/tests/test_measure_welding_splice_sleeve.py -v
```

Single test:

```bash
pytest backend/tests/test_measure_welding_splice_sleeve.py::test_inspection_welding_splice_dimensions -v
```

### Via npm (if backend venv is set up)

```bash
npm run test:backend
```

### What is covered

| Test | Purpose |
|------|---------|
| `test_inspection_welding_splice_dimensions` | Welding splice L/H on inspection sample |
| `test_inspection_sleeve_dimensions` | Sleeve L/H on inspection sample |
| `test_no_sleeve_capture_rejects_false_positive` | No false sleeve when only splices visible |
| `test_welding_splice_only_capture` | Splice-only image mode |
| `test_sleeve_only_capture` | Sleeve-only image (no splice) |
| `test_assembly_sleeve_excludes_wire_bundle` | Sleeve length not inflated by wires |
| `test_data_capture_sleeve_found` | All `Measurement/Data/capture*.png` |
| `test_sleeve_reference_profile_loaded` | Reference JSON present |
| `test_reference_mask_finds_full_tube_capture8` | Full tube on capture8 |

Requires: `opencv-python`, `numpy`, `pytest`, calibration folder, and optional golden PNGs under `backend/storage/`.

---

## Script map

| Script | Role |
|--------|------|
| `measure_welding_splice_sleeve.py` | Core CV: detection, measurement, CLI |
| `capture_measure_welding_splice_sleeve.py` | Live/file capture + both components |
| `capture_measure_welding_splice.py` | Live/file capture + welding splice only |
| `capture_measure_sleeve.py` | Live/file capture + heat-shrink sleeve only |
| `build_sleeve_reference.py` | Build sleeve color/geometry reference |
| `capture_common.py` | Shared capture CLI (API / camera / paths) |

### Deprecated (compatibility shims)

| Old path | Use instead |
|----------|-------------|
| `capture_measure_crimp_sleeve.py` | `capture_measure_welding_splice_sleeve.py` |
| `capture_measure_crimp.py` | `capture_measure_welding_splice.py` |
| `measure_crimp_sleeve.py` | `measure_welding_splice_sleeve.py` |

---

## Troubleshooting

| Symptom | Things to check |
|---------|------------------|
| Welding splice not found | Lighting, copper visibility, `_copper_mask` / barrel mask tuning |
| Sleeve length too large (includes wires) | Rebuild reference; check `test_assembly_sleeve_excludes_wire_bundle` |
| Sleeve not found | Run `build_sleeve_reference.py`; matte tube visible in band |
| Wrong mm values | Calibration session and `calibration.json` `px_per_mm` |
| API capture fails | Backend running, `VISION_URL`, network, `--api-key` if required |
| Tests skipped | Missing golden PNG paths; run from repo root |

---

## Quick reference (copy-paste)

```bash
# Assembly capture + measure
python3 scripts/capture_measure_welding_splice_sleeve.py \
  --calibration backend/storage/Calibration/session_20260517_081705

# Welding splice only
python3 scripts/capture_measure_welding_splice.py \
  --calibration backend/storage/Calibration/session_20260517_081705

# Offline measure one image
python3 scripts/measure_welding_splice_sleeve.py path/to/capture.png \
  --calibration backend/storage/Calibration/session_20260517_081705

# Tests
pytest backend/tests/test_measure_welding_splice_sleeve.py
```
