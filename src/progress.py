"""Progress + log emitter for the ClipForge web UI.

When enabled, emits machine-readable progress lines to stdout that the server
parses to drive the UI progress bar. Format:

    @@PROGRESS@@ {"percent": 12.3, "stage": "transcribe", "message": "..."}

When disabled (normal CLI use) every call is a no-op and normal `print` logs
remain the only output.
"""
import json

_enabled = False


def enable():
    global _enabled
    _enabled = True


def emit(percent, stage, message=""):
    if not _enabled:
        return
    payload = json.dumps({
        "percent": round(float(percent), 1),
        "stage": str(stage),
        "message": str(message),
    }, ensure_ascii=False)
    print(f"@@PROGRESS@@ {payload}", flush=True)


def event(kind, data=None):
    """Emit a discrete UI event (notification-worthy) without moving the
    progress bar. Only emitted when progress is enabled."""
    if not _enabled:
        return
    payload = json.dumps({"kind": str(kind), "data": data or {}},
                         ensure_ascii=False)
    print(f"@@EVENT@@ {payload}", flush=True)
