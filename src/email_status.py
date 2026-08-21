"""Persistent email-pipeline status for a source video.

One JSON file per video, stored beside its candidates file:
`<video_id>_email_status.json`. Tracks the outbound transcript send and the
inbound highlight reply so the web UI can show where the email pipeline is.

Status lifecycle: (none) -> awaiting -> received
"""
import json
from datetime import datetime, timezone
from pathlib import Path


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def status_path(candidates_dir, video_id):
    return Path(candidates_dir) / f"{video_id}_email_status.json"


def read_status(candidates_dir, video_id):
    try:
        data = json.loads(status_path(candidates_dir, video_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _write(candidates_dir, video_id, data):
    candidates_dir = Path(candidates_dir)
    candidates_dir.mkdir(parents=True, exist_ok=True)
    status_path(candidates_dir, video_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def mark_sent(candidates_dir, video_id, recipients, kind="cleaned", subject=""):
    prev = read_status(candidates_dir, video_id) or {}
    data = {
        "video_id": video_id,
        "status": "awaiting",
        "sent_at": _now(),
        "recipients": [r for r in (recipients or []) if r],
        "kind": kind,
        "subject": subject,
        "received_at": prev.get("received_at"),
        "clips_received": prev.get("clips_received"),
    }
    _write(candidates_dir, video_id, data)
    return data


def mark_received(candidates_dir, video_id, clips_count, source="email"):
    prev = read_status(candidates_dir, video_id) or {}
    data = dict(prev)
    data.update({
        "video_id": video_id,
        "status": "received",
        "received_at": _now(),
        "clips_received": int(clips_count or 0),
        "reply_source": source,
    })
    data.setdefault("sent_at", None)
    _write(candidates_dir, video_id, data)
    return data
