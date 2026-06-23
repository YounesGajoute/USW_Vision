#!/usr/bin/env python3
"""
Capture multiple images for ruler calibration.

Saves lossless PNG frames under backend/storage/Calibration/<session_id>/
with a manifest.json (dimensions, quality, timestamps).

Run on the vision Pi (camera must be free — stop the API if it holds the sensor):

  cd /home/bot/inspection_vision
  python3 scripts/capture_ruler_calibration.py

Interactive (default): press Enter to capture, q to quit.

  python3 scripts/capture_ruler_calibration.py --count 20 --interval 2

Use the REST API if the vision server already owns the camera (defaults to http://127.0.0.1:5000/api):

  python3 scripts/capture_ruler_calibration.py --via-api --count 10 --interval 1.5

Optional: set VISION_URL in backend/.env or pass --api-url http://192.168.10.2:5000
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent
_BACKEND_DIR = _REPO_ROOT / "backend"
_DEFAULT_OUTPUT = _BACKEND_DIR / "storage" / "Calibration"


def _add_backend_to_path() -> None:
    backend = str(_BACKEND_DIR)
    if backend not in sys.path:
        sys.path.insert(0, backend)


def _load_yaml_config(config_path: Path) -> dict:
    import yaml

    if not config_path.is_file():
        raise FileNotFoundError(f"Config not found: {config_path}")
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _resolve_output_dir(path: Optional[str]) -> Path:
    if path:
        out = Path(path).expanduser()
        if not out.is_absolute():
            out = (_REPO_ROOT / out).resolve()
    else:
        out = _DEFAULT_OUTPUT.resolve()
    return out


def _new_session_dir(base: Path) -> Path:
    session_id = datetime.now().strftime("session_%Y%m%d_%H%M%S")
    session_dir = base / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _lighting_on(lighting, settings: dict) -> bool:
    if not lighting or not lighting.is_ready():
        return False
    if not settings.get("during_capture", True):
        return False
    rgb = settings.get("default_rgb", [255, 255, 255])
    r, g, b = (int(rgb[0]) & 0xFF, int(rgb[1]) & 0xFF, int(rgb[2]) & 0xFF)
    lighting.fill(r, g, b)
    lighting.show()
    time.sleep(float(settings.get("settle_ms", 2.0)) / 1000.0)
    return True


def _lighting_off(lighting, settings: dict, applied: bool) -> None:
    if not applied or not lighting or not lighting.is_ready():
        return
    if not settings.get("off_after_capture", True):
        return
    lighting.off()


def _save_png(path: Path, image_rgb) -> None:
    import cv2

    if len(image_rgb.shape) == 3 and image_rgb.shape[2] == 3:
        bgr = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2BGR)
    else:
        bgr = image_rgb
    if not cv2.imwrite(str(path), bgr, [cv2.IMWRITE_PNG_COMPRESSION, 1]):
        raise RuntimeError(f"Failed to write {path}")


def _capture_local(
    camera,
    lighting,
    lighting_settings: dict,
    *,
    brightness_mode: str,
) -> Tuple[Any, dict]:
    from src.utils.image_processing import (
        capture_dimensions_meta,
        ensure_native_capture_rgb,
    )

    applied = _lighting_on(lighting, lighting_settings)
    try:
        image = camera.capture_image(brightness_mode=brightness_mode)
    finally:
        _lighting_off(lighting, lighting_settings, applied)

    if image is None:
        raise RuntimeError("Camera capture returned no frame")

    image, resized = ensure_native_capture_rgb(image)
    quality = camera.validate_image_quality(image)
    dims = capture_dimensions_meta(image)
    meta = {
        **dims,
        "resized_to_native": resized,
        "quality": quality,
        "brightness_mode": brightness_mode,
        "source": "local_camera",
    }
    return image, meta


def _capture_via_api(
    rest_base: str,
    remote_key: Optional[str],
    brightness_mode: str,
    *,
    local_key: Optional[str] = None,
) -> Tuple[bytes, dict]:
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("Install requests for --via-api: pip install requests") from exc

    headers = {"Content-Type": "application/json"}
    if local_key:
        headers["X-Vision-Local-Key"] = local_key
    elif remote_key:
        headers["X-Vision-Remote-Key"] = remote_key

    r = requests.post(
        f"{rest_base.rstrip('/')}/camera/capture",
        headers=headers,
        json={"brightnessMode": brightness_mode},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"API capture failed ({r.status_code}): {r.text[:500]}")

    data = r.json()
    img_b64 = data.get("image")
    if not img_b64:
        raise RuntimeError("API response missing image")

    raw = base64.b64decode(img_b64.split(",", 1)[-1])
    meta = {
        "width": data.get("width"),
        "height": data.get("height"),
        "isNativeResolution": data.get("isNativeResolution"),
        "nativeWidth": data.get("nativeWidth"),
        "nativeHeight": data.get("nativeHeight"),
        "quality": data.get("quality"),
        "brightness_mode": brightness_mode,
        "source": "api",
        "api_timestamp": data.get("timestamp"),
    }
    return raw, meta


def _init_hardware(config: dict):
    from src.hardware.camera import CameraController
    from src.hardware.p9813_lighting import (
        build_lighting_from_config,
        lighting_settings_from_yaml,
    )

    cam_cfg = config.get("camera") or {}
    slave_cfg = config.get("slave") or {}
    if "allow_test_pattern" in cam_cfg:
        allow_test_pattern = bool(cam_cfg["allow_test_pattern"])
    else:
        allow_test_pattern = not bool(slave_cfg.get("require_real_hardware", False))

    resolution = tuple(cam_cfg.get("resolution", [1456, 1088]))
    camera = CameraController(
        resolution=resolution,
        camera_device=cam_cfg.get("device", 0),
        allow_test_pattern=allow_test_pattern,
        isp_output_format=str(cam_cfg.get("isp_output_format", "RGB161616") or "RGB161616"),
    )
    lighting_cfg = config.get("lighting") or {}
    lighting = build_lighting_from_config(lighting_cfg)
    lighting_settings = lighting_settings_from_yaml(lighting_cfg)
    return camera, lighting, lighting_settings


def _write_manifest(session_dir: Path, manifest: dict) -> None:
    path = session_dir / "manifest.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)
        f.write("\n")


def _load_dotenv_files() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for env_path in (_BACKEND_DIR / ".env", _REPO_ROOT / ".env"):
        if env_path.is_file():
            load_dotenv(env_path, override=False)


def _default_api_port() -> int:
    port_env = os.environ.get("API_PORT", "").strip()
    if port_env.isdigit():
        return int(port_env)
    try:
        config = _load_yaml_config(_BACKEND_DIR / "config.yaml")
        return int((config.get("api") or {}).get("port", 5000))
    except Exception:
        return 5000


def _normalize_rest_base(url: str) -> str:
    base = url.strip().rstrip("/")
    return base if base.endswith("/api") else f"{base}/api"


def _resolve_api_base(cli_base: Optional[str]) -> Tuple[str, str]:
    """
    Returns (rest_base, source_description).
    rest_base is e.g. http://127.0.0.1:5000/api
    """
    if cli_base:
        return _normalize_rest_base(cli_base), "--api-url"

    _load_dotenv_files()

    url = os.environ.get("VISION_URL", "").strip()
    if url:
        return _normalize_rest_base(url), "VISION_URL"

    host = os.environ.get("API_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if host in ("0.0.0.0", "::"):
        host = "127.0.0.1"
    port = _default_api_port()
    default = f"http://{host}:{port}"
    return _normalize_rest_base(default), f"default ({default})"


def run_session(args: argparse.Namespace) -> int:
    output_base = _resolve_output_dir(args.output_dir)
    session_dir = _new_session_dir(output_base)
    session_id = session_dir.name

    manifest: Dict[str, Any] = {
        "purpose": "ruler_calibration",
        "session_id": session_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "output_dir": str(session_dir),
        "note": args.note or "",
        "brightness_mode": args.brightness_mode,
        "captures": [],
    }

    camera = None
    lighting = None
    lighting_settings: dict = {}

    if not args.via_api:
        _add_backend_to_path()
        config_path = Path(args.config).expanduser()
        if not config_path.is_absolute():
            config_path = (_BACKEND_DIR / config_path).resolve()
        config = _load_yaml_config(config_path)
        camera, lighting, lighting_settings = _init_hardware(config)

    api_base: Optional[str] = None
    api_source = ""
    if args.via_api:
        api_base, api_source = _resolve_api_base(args.api_url)

    _load_dotenv_files()
    remote_key = args.api_key or os.environ.get("VISION_REMOTE_API_KEY", "").strip() or None
    local_key = os.environ.get("VISION_LOCAL_API_KEY", "").strip() or None

    print("=" * 60)
    print("  Ruler calibration capture")
    print("=" * 60)
    print(f"  Session : {session_id}")
    print(f"  Folder  : {session_dir}")
    print(f"  Mode    : {'API' if args.via_api else 'local camera'}")
    if args.via_api and api_base:
        print(f"  API     : {api_base}  ({api_source})")
    if args.count == 0:
        print("  Controls: [Enter] capture  |  [q] quit")
    else:
        print(f"  Plan    : {args.count} image(s), interval {args.interval}s")
    print("=" * 60)

    captured = 0
    index = 0

    try:
        while True:
            if args.count > 0 and captured >= args.count:
                break

            if args.count == 0:
                try:
                    line = input(f"\n[{captured} saved] Press Enter to capture, q to quit: ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    print("\nStopped.")
                    break
                if line in ("q", "quit", "exit"):
                    break
            elif captured > 0 and args.interval > 0:
                time.sleep(args.interval)

            index += 1
            filename = f"cal_{index:03d}.png"
            out_path = session_dir / filename
            ts = datetime.now(timezone.utc).isoformat()

            try:
                if args.via_api:
                    raw, meta = _capture_via_api(
                        api_base,
                        remote_key,
                        args.brightness_mode,
                        local_key=local_key,
                    )
                    out_path.write_bytes(raw)
                else:
                    image, meta = _capture_local(
                        camera,
                        lighting,
                        lighting_settings,
                        brightness_mode=args.brightness_mode,
                    )
                    _save_png(out_path, image)
            except Exception as exc:
                print(f"  Capture {index} FAILED: {exc}", file=sys.stderr)
                manifest["captures"].append(
                    {
                        "index": index,
                        "filename": filename,
                        "timestamp": ts,
                        "error": str(exc),
                    }
                )
                _write_manifest(session_dir, manifest)
                if args.count > 0:
                    continue
                break

            entry = {
                "index": index,
                "filename": filename,
                "path": str(out_path),
                "timestamp": ts,
                **meta,
            }
            manifest["captures"].append(entry)
            _write_manifest(session_dir, manifest)
            captured += 1

            q = meta.get("quality") or {}
            w = meta.get("width", "?")
            h = meta.get("height", "?")
            score = q.get("score", q.get("overall", "?"))
            print(f"  Saved {filename}  ({w}x{h}, quality={score})")

    finally:
        if camera is not None:
            try:
                camera.close()
            except Exception:
                pass
        if lighting is not None:
            try:
                lighting.off()
            except Exception:
                pass

    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["capture_count"] = captured
    _write_manifest(session_dir, manifest)

    print("\n" + "=" * 60)
    print(f"  Done — {captured} image(s) in {session_dir}")
    print(f"  Manifest: {session_dir / 'manifest.json'}")
    print("=" * 60)
    return 0 if captured > 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture images for ruler calibration into storage/Calibration/",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output-dir",
        "-o",
        help=f"Base folder (default: {_DEFAULT_OUTPUT.relative_to(_REPO_ROOT)})",
    )
    parser.add_argument(
        "--count",
        "-n",
        type=int,
        default=0,
        help="Number of captures (0 = interactive until quit)",
    )
    parser.add_argument(
        "--interval",
        "-i",
        type=float,
        default=2.0,
        help="Seconds between auto captures (default: 2)",
    )
    parser.add_argument(
        "--brightness-mode",
        "-b",
        choices=["normal", "hdr", "highgain"],
        default="normal",
        help="Camera brightness preset (default: normal)",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Backend config.yaml path (local camera mode)",
    )
    parser.add_argument(
        "--via-api",
        action="store_true",
        help="Capture via POST /api/camera/capture (server must be running)",
    )
    parser.add_argument(
        "--api-url",
        help="Vision REST base (e.g. http://127.0.0.1:5000 or .../api)",
    )
    parser.add_argument(
        "--api-key",
        help="X-Vision-Remote-Key (default: VISION_REMOTE_API_KEY from backend/.env)",
    )
    parser.add_argument(
        "--note",
        help="Optional note stored in manifest.json (e.g. 'tape position A')",
    )
    args = parser.parse_args()
    if args.count < 0:
        parser.error("--count must be >= 0")
    if args.interval < 0:
        parser.error("--interval must be >= 0")
    return run_session(args)


if __name__ == "__main__":
    sys.exit(main())
