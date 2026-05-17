#!/usr/bin/env python3
"""
Control a Vision Inspection slave Raspberry Pi from a master over the network.

Run on the **master** (US Machine), not on the vision Pi, unless testing locally on the slave.

REST base URL resolution (first match wins):
  1. --base-url
  2. VISION_URL in backend/.env (recommended: http://<vision-ip>:5000, no /api suffix)
  3. VISION_SLAVE_URL (deprecated; ignored if it points at 127.0.0.1/localhost on the master)

Also loads VISION_REMOTE_KEY from backend/.env when --key is not passed.

Examples:
  ./scripts/vision-master.sh check
  ./scripts/vision-master.sh capture --out shot.png
  ./scripts/vision-master.sh register-master 11
  ./scripts/vision-master.sh create-template "Line A" --tools tools.json
  ./scripts/vision-master.sh run-once 11
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

try:
    import requests
except ImportError:
    print("Install requests: pip install requests", file=sys.stderr)
    sys.exit(1)

_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent


def _load_dotenv() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    for path in (_REPO_ROOT / "backend" / ".env", _REPO_ROOT / ".env"):
        if path.is_file():
            load_dotenv(path, override=False)


def _strip_env(name: str) -> Optional[str]:
    val = os.environ.get(name)
    if val is None:
        return None
    val = val.strip()
    return val or None


def _is_loopback_host(host: Optional[str]) -> bool:
    if not host:
        return False
    h = host.lower()
    return h in ("127.0.0.1", "localhost", "::1")


def normalize_rest_base(url: str) -> str:
    """VISION_URL=http://host:5000 → http://host:5000/api ; preserve /api/v1."""
    raw = url.strip().rstrip("/")
    if not raw:
        raise ValueError("empty vision base URL")
    lower = raw.lower()
    if lower.endswith("/api/v1"):
        return raw
    if lower.endswith("/api"):
        return raw
    return f"{raw}/api"


def _vision_url_from_env() -> Optional[str]:
    vision_url = _strip_env("VISION_URL")
    if vision_url:
        return vision_url

    slave = _strip_env("VISION_SLAVE_URL")
    if not slave:
        return None
    parsed = urlparse(slave if "://" in slave else f"http://{slave}")
    if _is_loopback_host(parsed.hostname):
        print(
            "warning: VISION_SLAVE_URL points at localhost; "
            "the vision API is not on the master. "
            "Set VISION_URL in backend/.env and unset VISION_SLAVE_URL.",
            file=sys.stderr,
        )
        return None
    return slave


def resolve_rest_base(cli_base: Optional[str]) -> Tuple[str, str]:
    """
    Returns (rest_base, source_description).
    rest_base is e.g. http://192.168.10.2:5000/api
    """
    _load_dotenv()

    if cli_base:
        return normalize_rest_base(cli_base), "--base-url"

    raw = _vision_url_from_env()
    if raw:
        source = "VISION_URL" if _strip_env("VISION_URL") else "VISION_SLAVE_URL"
        return normalize_rest_base(raw), source

    raise SystemExit(
        "No vision slave URL configured.\n"
        "On the master, set backend/.env:\n"
        "  VISION_URL=http://192.168.10.2:5000\n"
        "  VISION_REMOTE_KEY=<same as vision VISION_REMOTE_API_KEY>\n"
        "Then: unset VISION_SLAVE_URL\n"
        "Pick an IP the master can ping (see docs/MASTER_VISION_CONNECTIVITY.md)."
    )


def resolve_remote_key(cli_key: Optional[str]) -> Optional[str]:
    if cli_key:
        return cli_key
    _load_dotenv()
    return _strip_env("VISION_REMOTE_KEY")


def resolve_local_key() -> Optional[str]:
    _load_dotenv()
    return _strip_env("VISION_LOCAL_KEY")


def _http_root(rest_base: str) -> str:
    root = rest_base.rstrip("/")
    if root.endswith("/api/v1"):
        return root[: -len("/api/v1")]
    if root.endswith("/api"):
        return root[: -len("/api")]
    return root


def _headers(
    remote_key: Optional[str] = None,
    *,
    json_body: bool = True,
    local_key: Optional[str] = None,
) -> Dict[str, str]:
    h: Dict[str, str] = {}
    if json_body:
        h["Content-Type"] = "application/json"
    if remote_key:
        h["X-Vision-Remote-Key"] = remote_key
    lk = local_key if local_key is not None else resolve_local_key()
    if lk:
        h["X-Vision-Local-Key"] = lk
    return h


def _host_from_rest_base(rest_base: str) -> str:
    parsed = urlparse(_http_root(rest_base))
    return parsed.hostname or ""


def diagnose_request_error(exc: requests.RequestException, rest_base: str) -> None:
    host = _host_from_rest_base(rest_base)
    print(f"\nTarget: {rest_base}", file=sys.stderr)
    if _is_loopback_host(host):
        print(
            "Diagnosis: 127.0.0.1 / localhost only works ON the vision Pi itself.\n"
            "On the master, set VISION_URL to the vision Pi's LAN or Tailscale IP.",
            file=sys.stderr,
        )
        return

    err = str(exc).lower()
    if "connection refused" in err or "errno 111" in err:
        print(
            f"Diagnosis: nothing accepted TCP on {host}:5000 "
            "(service down, wrong IP, or firewall).",
            file=sys.stderr,
        )
    elif "no route to host" in err or "errno 113" in err:
        print(
            f"Diagnosis: master cannot reach {host} at L3 "
            "(wrong subnet, cable/WiFi, or static IP not set on vision Pi).",
            file=sys.stderr,
        )
    elif "network is unreachable" in err or "destination host unreachable" in err:
        print(
            f"Diagnosis: no route to {host}. "
            "Master and vision Pi are likely on different subnets "
            "(e.g. master 192.168.10.x vs vision WiFi 192.168.100.x).",
            file=sys.stderr,
        )
    print(
        "Fix: choose a path in docs/MASTER_VISION_CONNECTIVITY.md "
        "(Ethernet 192.168.10.2, reachable WiFi IP, or Tailscale).",
        file=sys.stderr,
    )


def cmd_capture(
    rest_base: str,
    remote_key: Optional[str],
    out_path: str,
    brightness_mode: str,
) -> int:
    r = requests.post(
        f"{rest_base}/camera/capture",
        headers=_headers(remote_key),
        json={"brightnessMode": brightness_mode},
        timeout=120,
    )
    if r.status_code != 200:
        print(r.text, file=sys.stderr)
        r.raise_for_status()
    data = r.json()
    img_b64 = data.get("image")
    if not img_b64:
        print("No image in capture response", file=sys.stderr)
        return 1
    raw = base64.b64decode(img_b64.split(",", 1)[-1])
    out = Path(out_path)
    out.write_bytes(raw)
    print(json.dumps({k: v for k, v in data.items() if k != "image"}, indent=2))
    print(f"Saved capture: {out} ({len(raw)} bytes)", file=sys.stderr)
    return 0


def cmd_register_master(
    rest_base: str,
    remote_key: Optional[str],
    program_id: int,
    brightness_mode: str,
    image_path: Optional[str],
) -> int:
    """Capture from vision camera (or use --image) and POST multipart /master-image."""
    if image_path:
        raw = Path(image_path).read_bytes()
        filename = Path(image_path).name
    else:
        r = requests.post(
            f"{rest_base}/camera/capture",
            headers=_headers(remote_key),
            json={"brightnessMode": brightness_mode},
            timeout=120,
        )
        if r.status_code != 200:
            print(r.text, file=sys.stderr)
            r.raise_for_status()
        cap = r.json()
        img_b64 = cap.get("image")
        if not img_b64:
            print("Capture returned no image", file=sys.stderr)
            return 1
        raw = base64.b64decode(img_b64.split(",", 1)[-1])
        filename = f"master_p{program_id}.png"
        print(json.dumps({k: v for k, v in cap.items() if k != "image"}, indent=2))

    files = {"file": (filename, raw, "image/png")}
    data = {"programId": str(program_id)}
    h = _headers(remote_key, json_body=False)
    r = requests.post(f"{rest_base}/master-image", headers=h, files=files, data=data, timeout=120)
    if r.status_code != 200:
        print(r.text, file=sys.stderr)
        r.raise_for_status()
    print(json.dumps(r.json(), indent=2))
    print(f"Master image registered for program {program_id}", file=sys.stderr)
    return 0


def cmd_create_template(
    rest_base: str,
    remote_key: Optional[str],
    name: str,
    tools_path: str,
    description: str,
) -> int:
    tools_file = Path(tools_path)
    if not tools_file.is_file():
        print(f"Tools file not found: {tools_file}", file=sys.stderr)
        return 1
    with tools_file.open(encoding="utf-8") as f:
        payload = json.load(f)
    tools = payload.get("tools") if isinstance(payload, dict) and "tools" in payload else payload
    if not isinstance(tools, list):
        print("JSON must be a tools array or {\"tools\": [...]}", file=sys.stderr)
        return 1
    body: Dict[str, Any] = {
        "name": name,
        "tools": tools,
        "description": description or (payload.get("description", "") if isinstance(payload, dict) else ""),
    }
    if isinstance(payload, dict) and payload.get("roi_space"):
        body["roi_space"] = payload["roi_space"]
    r = requests.post(
        f"{rest_base}/tool-templates",
        headers=_headers(remote_key),
        json=body,
        timeout=60,
    )
    if r.status_code not in (200, 201):
        print(r.text, file=sys.stderr)
        r.raise_for_status()
    print(json.dumps(r.json(), indent=2))
    return 0


def cmd_check(rest_base: str, key: Optional[str]) -> int:
    host = _host_from_rest_base(rest_base)
    port = urlparse(_http_root(rest_base)).port or 5000
    print(f"REST base: {rest_base}")
    print(f"Host:      {host}:{port}")

    if _is_loopback_host(host):
        print("WARN: loopback URL — use only for tests on the vision Pi itself.", file=sys.stderr)

    # Layer 3 hint (ICMP may be blocked; still useful on LAN)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(3.0)
            code = sock.connect_ex((host, port))
        if code == 0:
            print(f"TCP:       port {port} open on {host}")
        elif code == 111:
            print(f"TCP:       connection refused on {host}:{port} (nothing listening)", file=sys.stderr)
        elif code in (113, 101):
            print(f"TCP:       no route to {host}:{port}", file=sys.stderr)
        else:
            print(f"TCP:       connect failed (errno {code})", file=sys.stderr)
    except OSError as e:
        print(f"TCP:       {e}", file=sys.stderr)

    try:
        r = requests.get(f"{rest_base}/remote/info", headers=_headers(key), timeout=15)
        r.raise_for_status()
        print("HTTP:      GET /remote/info →", r.status_code)
        print(json.dumps(r.json(), indent=2))
        return 0
    except requests.RequestException as e:
        print(f"HTTP:      failed — {e}", file=sys.stderr)
        diagnose_request_error(e, rest_base)
        return 1


def cmd_info(base: str, key: Optional[str]) -> None:
    r = requests.get(f"{base}/remote/info", headers=_headers(key), timeout=15)
    r.raise_for_status()
    print(json.dumps(r.json(), indent=2))


def cmd_programs(base: str, key: Optional[str]) -> None:
    r = requests.get(f"{base}/programs?active_only=true", headers=_headers(key), timeout=30)
    r.raise_for_status()
    data = r.json()
    for p in data.get("programs", []):
        print(f"{p.get('id')}\t{p.get('name')}")


def cmd_run_once(base: str, key: Optional[str], program_id: int, no_image: bool) -> None:
    body: Dict[str, Any] = {
        "programId": program_id,
        "triggerType": "remote",
        "includeImage": not no_image,
    }
    r = requests.post(
        f"{base}/remote/inspection/run-once",
        headers=_headers(key),
        json=body,
        timeout=120,
    )
    if r.status_code != 200:
        print(r.text, file=sys.stderr)
        r.raise_for_status()
    data = r.json()
    img = data.pop("image", None)
    print(json.dumps(data, indent=2))
    if img and not no_image:
        out = f"inspection_p{program_id}_{int(time.time())}.jpg"
        raw = base64.b64decode(img)
        with open(out, "wb") as f:
            f.write(raw)
        print(f"Saved image: {out}", file=sys.stderr)


def cmd_socket_loop(base_http: str, key: Optional[str], program_id: int, continuous: bool, fps: int) -> None:
    try:
        import socketio
    except ImportError:
        print('Install: pip install "python-socketio[client]"', file=sys.stderr)
        sys.exit(1)

    root = _http_root(base_http)

    sio = socketio.Client(logger=False, engineio_logger=False)

    @sio.event
    def connect():
        print("Socket.IO connected", file=sys.stderr)

    @sio.event
    def disconnect():
        print("Socket.IO disconnected", file=sys.stderr)

    @sio.on("inspection_result")
    def on_result(data: Dict[str, Any]):
        d = dict(data)
        if "image" in d and len(str(d.get("image", ""))) > 80:
            d["image"] = f"<base64 len={len(str(data.get('image')))}>"
        print("inspection_result:", json.dumps(d, indent=2))

    @sio.on("live_frame")
    def on_frame(data: Dict[str, Any]):
        print(
            "live_frame",
            data.get("frameNumber"),
            "fps",
            data.get("fps"),
            "latencyMs",
            data.get("latencyMs"),
            file=sys.stderr,
        )

    @sio.on("error")
    def on_error(data: Dict[str, Any]):
        print("error:", data, file=sys.stderr)

    hdr: Dict[str, str] = {}
    if key:
        hdr["X-Vision-Remote-Key"] = key
    connect_kw: Dict[str, Any] = {
        "socketio_path": "/socket.io/",
        "transports": ["websocket", "polling"],
    }
    if hdr:
        connect_kw["headers"] = hdr
    if key:
        connect_kw["auth"] = {"remoteKey": key}
    sio.connect(root, **connect_kw)

    sio.emit("start_inspection", {"programId": program_id, "continuous": continuous})
    sio.emit("subscribe_live_feed", {"fps": max(1, min(60, fps))})

    try:
        if continuous:
            print("Running until Ctrl+C (continuous inspection + live feed)...", file=sys.stderr)
            sio.wait()
        else:
            print("Waiting for single inspection + frames (10 s)...", file=sys.stderr)
            time.sleep(10.0)
    except KeyboardInterrupt:
        pass
    finally:
        sio.emit("unsubscribe_live_feed")
        sio.emit("stop_inspection")
        sio.disconnect()


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(
        description="Vision inspection slave — remote master client (run on US Machine master)",
    )
    p.add_argument(
        "--base-url",
        default=None,
        help="Override REST base (default: VISION_URL from backend/.env → .../api)",
    )
    p.add_argument(
        "--key",
        default=None,
        help="X-Vision-Remote-Key (default: VISION_REMOTE_KEY from backend/.env)",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="TCP + GET /remote/info with connectivity hints")
    sub.add_parser("info", help="GET /remote/info — discovery")
    sub.add_parser("programs", help="List programs")

    cap = sub.add_parser("capture", help="POST /camera/capture — save live frame to disk")
    cap.add_argument("--out", default="vision_capture.png", help="Output image path")
    cap.add_argument(
        "--brightness",
        default="normal",
        choices=["normal", "hdr", "highgain"],
        help="Camera brightness preset",
    )

    reg = sub.add_parser(
        "register-master",
        help="Capture (or --image) and POST /master-image for a program",
    )
    reg.add_argument("program_id", type=int, help="Program id on vision Pi")
    reg.add_argument("--image", help="Use existing PNG/JPEG instead of live capture")
    reg.add_argument("--brightness", default="normal", choices=["normal", "hdr", "highgain"])

    tpl = sub.add_parser("create-template", help="POST /tool-templates from JSON tools file")
    tpl.add_argument("name", help="Template name")
    tpl.add_argument("--tools", required=True, help="JSON file: tools[] or {tools:[]}")
    tpl.add_argument("--description", default="", help="Optional description")

    r1 = sub.add_parser("run-once", help="POST /remote/inspection/run-once")
    r1.add_argument("program_id", type=int)
    r1.add_argument("--no-image", action="store_true", help="JSON only, no base64 image")

    sk = sub.add_parser("socket", help="Socket.IO: continuous or single inspection + live feed")
    sk.add_argument("program_id", type=int)
    sk.add_argument("--single", action="store_true", help="Single shot (waits ~10s)")
    sk.add_argument("--fps", type=int, default=12)

    args = p.parse_args(argv)
    rest_base, source = resolve_rest_base(args.base_url)
    key = resolve_remote_key(args.key)
    if source != "--base-url":
        print(f"Using {source} → {rest_base}", file=sys.stderr)

    try:
        if args.cmd == "check":
            return cmd_check(rest_base, key)
        if args.cmd == "info":
            cmd_info(rest_base, key)
        elif args.cmd == "programs":
            cmd_programs(rest_base, key)
        elif args.cmd == "capture":
            return cmd_capture(rest_base, key, args.out, args.brightness)
        elif args.cmd == "register-master":
            return cmd_register_master(
                rest_base, key, args.program_id, args.brightness, args.image
            )
        elif args.cmd == "create-template":
            return cmd_create_template(rest_base, key, args.name, args.tools, args.description)
        elif args.cmd == "run-once":
            cmd_run_once(rest_base, key, args.program_id, args.no_image)
        elif args.cmd == "socket":
            cmd_socket_loop(rest_base, key, args.program_id, continuous=not args.single, fps=args.fps)
        return 0
    except requests.RequestException as e:
        print(e, file=sys.stderr)
        diagnose_request_error(e, rest_base)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
