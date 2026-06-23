"""Shared capture helpers for welding-splice / heat-shrink-sleeve capture scripts."""

from __future__ import annotations

import argparse
import base64
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

import cv2
import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_BACKEND = _REPO_ROOT / "backend"
DEFAULT_MEASUREMENT_ROOT = _BACKEND / "storage" / "Measurement"


def load_dotenv() -> None:
    try:
        from dotenv import load_dotenv as _load
    except ImportError:
        return
    for p in (_BACKEND / ".env", _REPO_ROOT / ".env"):
        if p.is_file():
            _load(p, override=False)


def resolve_api_base(cli: Optional[str]) -> Tuple[str, str]:
    if cli:
        b = cli.strip().rstrip("/")
        return (b if b.endswith("/api") else f"{b}/api"), "--api-url"
    load_dotenv()
    url = os.environ.get("VISION_URL", "").strip()
    if url:
        url = url.rstrip("/")
        base = url if url.endswith("/api") else f"{url}/api"
        return base, "VISION_URL"
    return "http://127.0.0.1:5000/api", "default (http://127.0.0.1:5000)"


def capture_via_api(
    api_base: str,
    remote_key: Optional[str],
    brightness_mode: str,
) -> Tuple[np.ndarray, dict]:
    try:
        import requests
    except ImportError as exc:
        raise SystemExit("pip install requests") from exc

    headers = {"Content-Type": "application/json"}
    if remote_key:
        headers["X-Vision-Remote-Key"] = remote_key

    r = requests.post(
        f"{api_base.rstrip('/')}/camera/capture",
        headers=headers,
        json={"brightnessMode": brightness_mode},
        timeout=120,
    )
    if r.status_code != 200:
        raise SystemExit(f"Capture failed ({r.status_code}): {r.text[:400]}")

    data = r.json()
    img_b64 = data.get("image")
    if not img_b64:
        raise SystemExit("API returned no image")

    raw = base64.b64decode(img_b64.split(",", 1)[-1])
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit("Failed to decode capture PNG")
    return img, data


def capture_local(brightness_mode: str, config_path: Path) -> np.ndarray:
    import yaml
    from src.hardware.camera import CameraController
    from src.hardware.p9813_lighting import (
        build_lighting_from_config,
        lighting_settings_from_yaml,
    )
    from src.utils.image_processing import ensure_native_capture_rgb

    cfg_path = config_path if config_path.is_file() else _BACKEND / "config.yaml"
    with open(cfg_path, encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    cam_cfg = config.get("camera") or {}
    camera = CameraController(
        resolution=tuple(cam_cfg.get("resolution", [1456, 1088])),
        camera_device=cam_cfg.get("device", 0),
        allow_test_pattern=bool(cam_cfg.get("allow_test_pattern", True)),
        isp_output_format=str(cam_cfg.get("isp_output_format", "RGB161616") or "RGB161616"),
    )
    lighting = build_lighting_from_config(config.get("lighting") or {})
    settings = lighting_settings_from_yaml(config.get("lighting") or {})

    applied = False
    if lighting and lighting.is_ready() and settings.get("during_capture", True):
        rgb = settings.get("default_rgb", [255, 255, 255])
        lighting.fill(int(rgb[0]), int(rgb[1]), int(rgb[2]))
        lighting.show()
        import time

        time.sleep(float(settings.get("settle_ms", 2.0)) / 1000.0)
        applied = True

    try:
        frame = camera.capture_image(brightness_mode=brightness_mode)
    finally:
        if applied and lighting and settings.get("off_after_capture", True):
            lighting.off()
        camera.close()

    if frame is None:
        raise SystemExit("Local capture failed (is another process using the camera?)")

    rgb, _ = ensure_native_capture_rgb(frame)
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def add_capture_cli(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--image", type=Path, help="Skip capture; measure this file")
    parser.add_argument(
        "--output-dir",
        "-o",
        type=Path,
        help=f"Output folder (default: storage/Measurement/session_…)",
    )
    parser.add_argument(
        "--calibration",
        "-c",
        type=Path,
        help="Calibration session with calibration.json",
    )
    parser.add_argument("--px-per-mm", type=float, help="Override scale")
    parser.add_argument(
        "--local-camera",
        action="store_true",
        help="Capture with local Picamera2 (else API)",
    )
    parser.add_argument("--api-url", help="Vision API base URL")
    parser.add_argument("--api-key", help="X-Vision-Remote-Key")
    parser.add_argument(
        "--brightness-mode",
        choices=["normal", "hdr", "highgain"],
        default="normal",
    )
    parser.add_argument("--note", default="", help="Stored in measurement JSON")


def resolve_output_dir(args: argparse.Namespace) -> Path:
    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser().resolve()
    else:
        sid = datetime.now().strftime("session_%Y%m%d_%H%M%S")
        out_dir = (DEFAULT_MEASUREMENT_ROOT / sid).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def acquire_frame(
    args: argparse.Namespace,
    out_dir: Path,
) -> Tuple[np.ndarray, str, str, dict]:
    """Return (image_bgr, image_label, stem, capture_meta)."""
    capture_meta: dict = {}
    if args.image:
        img_path = args.image.resolve()
        img = cv2.imread(str(img_path))
        if img is None:
            raise SystemExit(f"Cannot read {img_path}")
        capture_meta["source"] = "file"
        return img, str(img_path), img_path.stem, capture_meta

    load_dotenv()
    key = args.api_key or os.environ.get("VISION_REMOTE_API_KEY", "").strip() or None
    if args.local_camera:
        print("Capturing (local camera)...")
        img = capture_local(args.brightness_mode, _BACKEND / "config.yaml")
        capture_meta["source"] = "local_camera"
    else:
        api_base, api_src = resolve_api_base(args.api_url)
        print(f"Capturing via API {api_base} ({api_src}) ...")
        img, capture_meta = capture_via_api(api_base, key, args.brightness_mode)
        capture_meta["source"] = "api"

    return img, str(out_dir / "capture.png"), "capture", capture_meta


def merge_capture_json(json_path: Path, capture_meta: dict, note: str) -> None:
    import json

    with open(json_path, encoding="utf-8") as f:
        payload = json.load(f)
    payload["capture"] = capture_meta
    if note:
        payload["note"] = note
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
        f.write("\n")


def print_artifacts(files: dict, tune_hint: str) -> None:
    print("\nArtifacts:")
    for name, path in sorted(files.items()):
        print(f"  {name}: {path}")
    print(f"\n{tune_hint}")
