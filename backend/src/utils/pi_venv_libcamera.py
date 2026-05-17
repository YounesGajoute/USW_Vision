"""Make apt-installed python3-libcamera / python3-picamera2 visible in a venv.

On Raspberry Pi OS, those packages live under /usr/lib/python3.x/dist-packages. A
normal venv does not see them, so imports fail. We append that path (not prepend) so
venv wheels (numpy, opencv, etc.) stay ahead while `libcamera` and `picamera2` still
resolve from the OS — matching versions from one `apt install python3-picamera2`.

Avoid `pip install picamera2` on the Pi: it shadows apt and often breaks against
system libcamera (e.g. CameraConfiguration API mismatches).
"""

from __future__ import annotations

import sys
from pathlib import Path


def ensure_system_libcamera_path() -> None:
    if sys.prefix == sys.base_prefix:
        return
    ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    candidates = [
        Path(f"/usr/lib/python{ver}/dist-packages"),
        Path("/usr/lib/python3/dist-packages"),
        Path(f"/usr/local/lib/python{ver}/dist-packages"),
    ]
    for root in candidates:
        if not root.is_dir():
            continue
        has_libcamera = (root / "libcamera").exists() or any(root.glob("libcamera*.so"))
        if not has_libcamera:
            continue
        s = str(root.resolve())
        if s not in sys.path:
            sys.path.append(s)
        break
