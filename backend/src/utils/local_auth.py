"""Optional local API key for inspection/GPIO/program routes on networked Pi."""

import secrets
from functools import wraps
from typing import Any, Callable

from flask import current_app, jsonify, request


def verify_local_api_key() -> bool:
    """Return True if request is allowed (no key configured, or key matches)."""
    expected = (current_app.config.get("LOCAL_API_KEY") or "").strip()
    if not expected:
        return True
    header = (request.headers.get("X-Vision-Local-Key") or "").strip()
    if header and secrets.compare_digest(header, expected):
        return True
    auth = (request.headers.get("Authorization") or "").strip()
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        if token and secrets.compare_digest(token, expected):
            return True
    return False


def require_local_api_key(f: Callable[..., Any]) -> Callable[..., Any]:
    """Reject when LOCAL_API_KEY is set and caller cannot authenticate."""

    @wraps(f)
    def decorated(*args: Any, **kwargs: Any):
        if current_app.config.get("SLAVE_REQUIRE_LOCAL_API_KEY"):
            expected = (current_app.config.get("LOCAL_API_KEY") or "").strip()
            if not expected:
                return (
                    jsonify(
                        {
                            "error": "Server misconfiguration",
                            "detail": "slave.require_local_api_key is true but local.api_key is empty",
                        }
                    ),
                    503,
                )
        if not verify_local_api_key():
            return jsonify(
                {"error": "Unauthorized", "hint": "Send X-Vision-Local-Key or Authorization: Bearer"}
            ), 401
        return f(*args, **kwargs)

    return decorated
