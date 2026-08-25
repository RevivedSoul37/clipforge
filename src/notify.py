"""Telegram notifications for ClipForge.

One sink subscribed to the web event bus (server.publish_event): every
lifecycle/outcome event is formatted as a small HTML message and delivered
via the Telegram Bot API over plain urllib (same zero-dependency pattern as
src/llm_client.py).

Delivery is non-blocking: send_telegram() enqueues onto a queue.Queue drained
by a single daemon thread, so publish_event never waits on the network.
Failures are swallowed with one logged warning line. Secrets (bot token,
chat id) live only in .env and are never logged, rendered or returned by any
API (logs show a masked token at most).
"""
import html
import json
import queue
import threading
import urllib.error
import urllib.parse
import urllib.request

from src.config import config

API_BASE = "https://api.telegram.org"
SEND_TIMEOUT = 10

KIND_EMOJI = {
    "run_started": "⏳",
    "run_ok": "✅",
    "run_error": "❌",
    "run_cancelled": "⏹",
    "awaiting_highlights": "💬",
    "highlights_received": "💬",
    "transcript_sent": "💬",
    "export_done": "🎬",
    "explore_done": "🎨",
    "upload_done": "⬆",
    "campaign_created": "📁",
}

_queue = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()
_misconfig_warned = False


# --------------------------------------------------------------------------- #
# Telegram API (urllib only)
# --------------------------------------------------------------------------- #
def _api_call(token, method, payload=None, method_get=False, timeout=SEND_TIMEOUT):
    """Call a Bot API method; returns the parsed JSON response (dict)."""
    url = f"{API_BASE}/bot{token}/{method}"
    if method_get:
        qs = urllib.parse.urlencode(payload or {})
        req = urllib.request.Request(f"{url}?{qs}")
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload or {}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_message_sync(token, chat_id, text):
    """Blocking sendMessage (used by the telegram-test CLI)."""
    return _api_call(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    })


def get_updates_sync(token):
    """Blocking getUpdates (used by the telegram-setup CLI)."""
    return _api_call(token, "getUpdates",
                     {"allowed_updates": '["message"]'}, method_get=True)


def _masked_token(token):
    return f"{token[:6]}…" if token else ""


# --------------------------------------------------------------------------- #
# delivery queue (non-blocking)
# --------------------------------------------------------------------------- #
def configured():
    return bool(config.telegram_enabled and config.telegram_bot_token
                and config.telegram_chat_id)


def _warn_misconfigured():
    global _misconfig_warned
    if _misconfig_warned:
        return
    _misconfig_warned = True
    if not config.telegram_enabled:
        return  # master toggle off = intentional silence
    print("[notify] telegram disabled: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID "
          "missing in .env (run `python main.py telegram-setup`)", flush=True)


def _ensure_worker():
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        threading.Thread(target=_worker, name="notify-telegram",
                         daemon=True).start()


def _worker():
    while True:
        text = _queue.get()
        try:
            _api_call(config.telegram_bot_token, "sendMessage", {
                "chat_id": config.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            })
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] telegram send failed: {exc}", flush=True)


def send_telegram(title, lines, kind="info", campaign_id=None):
    """Enqueue a notification; returns immediately. Never raises.

    `title` and every entry of `lines` are escaped before being wrapped in
    Telegram HTML tags, so user-supplied text (video names, hooks) is safe.
    """
    try:
        if not configured():
            _warn_misconfigured()
            return False
        emoji = KIND_EMOJI.get(kind, "🔔")
        parts = [f"{emoji} <b>{html.escape(str(title))}</b>"]
        for line in lines:
            line = str(line).strip()
            if line:
                parts.append(f"• {html.escape(line)}")
        cid = campaign_id or getattr(config, "active_campaign_id", None)
        camp_name = _campaign_name(cid) or cid or ""
        if camp_name:
            parts.append(f"<i>ClipForge · {html.escape(str(camp_name))}</i>")
        _queue.put("\n".join(parts))
        _ensure_worker()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] telegram enqueue failed: {exc}", flush=True)
        return False


def _campaign_name(campaign_id):
    """Best-effort campaign name for the message footer; never raises."""
    if not campaign_id:
        return ""
    try:
        from src import campaigns as camp_mod
        camp = camp_mod.get_campaign(campaign_id)
        return (camp.meta.get("name") or campaign_id) if camp else ""
    except Exception:  # noqa: BLE001
        return ""


# --------------------------------------------------------------------------- #
# event formatters (tolerant of missing fields; return (title, [lines]))
# --------------------------------------------------------------------------- #
def _fmt_run_started(d):
    mode = d.get("mode") or "run"
    return (f"{mode} started for {d.get('video') or d.get('video_id') or '?'}",
            [])


def _fmt_run_ok(d):
    mode = d.get("mode") or "run"
    lines = []
    video_id = d.get("video_id")
    if video_id:
        lines.append(f"video: {video_id}")
    winner = _exploration_winner(video_id)
    if winner:
        lines.append(winner)
    return f"{mode} finished", lines


def _fmt_run_error(d):
    mode = d.get("mode") or "run"
    lines = []
    err = str(d.get("error") or "").strip()
    if err:
        lines.append(err[-300:])
    if d.get("video_id"):
        lines.append(f"video: {d['video_id']}")
    return f"{mode} failed", lines


def _fmt_run_cancelled(d):
    mode = d.get("mode") or "run"
    lines = []
    if d.get("video_id"):
        lines.append(f"video: {d['video_id']}")
    return f"{mode} cancelled", lines


def _fmt_awaiting_highlights(d):
    lines = []
    if d.get("video_id"):
        lines.append(f"video: {d['video_id']}")
    for r in _email_recipients(d.get("video_id")):
        lines.append(f"to: {r}")
    if d.get("sent") is False:
        lines.append("email failed — check SMTP settings")
    return "Transcript emailed", lines


def _fmt_highlights_received(d):
    lines = []
    if d.get("video_id"):
        lines.append(f"video: {d['video_id']}")
    if d.get("clip_count") is not None:
        lines.append(f"{d['clip_count']} highlight(s)")
    return "Highlights received", lines


def _fmt_export_done(d):
    lines = []
    if d.get("video_id"):
        lines.append(f"video: {d['video_id']}")
    if d.get("clip_count") is not None:
        lines.append(f"{d['clip_count']} clip(s)")
    names = d.get("names") or []
    for name in names[:5]:
        lines.append(name)
    if len(names) > 5:
        lines.append(f"+{len(names) - 5} more")
    return "Export finished", lines


def _fmt_explore_done(d):
    lines = []
    if d.get("video_id"):
        lines.append(f"video: {d['video_id']}")
    if d.get("winner"):
        total = d.get("total")
        score = f" ({float(total):.1f})" if total is not None else ""
        lines.append(f"winner: {d['winner']}{score}")
    return "Style exploration finished", lines


def _fmt_upload_done(d):
    lines = []
    if d.get("name"):
        lines.append(d["name"])
    size = d.get("size")
    if size:
        lines.append(f"{float(size) / 1048576:.1f} MB")
    return "Video uploaded", lines


def _fmt_campaign_created(d):
    lines = []
    if d.get("id"):
        lines.append(f"id: {d['id']}")
    return f"Campaign created: {d.get('name') or '?'}", lines


FORMATTERS = {
    "run_started": _fmt_run_started,
    "run_ok": _fmt_run_ok,
    "run_error": _fmt_run_error,
    "run_cancelled": _fmt_run_cancelled,
    "awaiting_highlights": _fmt_awaiting_highlights,
    "highlights_received": _fmt_highlights_received,
    "export_done": _fmt_export_done,
    "explore_done": _fmt_explore_done,
    "upload_done": _fmt_upload_done,
    "campaign_created": _fmt_campaign_created,
}


def _exploration_winner(video_id):
    """Best-effort 'winner style' line for run_ok; never raises."""
    if not video_id:
        return ""
    try:
        from src import style_explorer
        path = style_explorer.report_path_for(video_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        winner = data.get("winner")
        return f"style: {winner}" if winner else ""
    except Exception:  # noqa: BLE001
        return ""


def _email_recipients(video_id):
    """Best-effort recipient list from the email-status record; never raises."""
    if not video_id:
        return []
    try:
        from src import email_status
        status = email_status.read_status(config.candidates_dir, video_id)
        sent = (status or {}).get("sent") or {}
        return sent.get("recipients") or []
    except Exception:  # noqa: BLE001
        return []


def handle_event(kind, data):
    """Format + enqueue one bus event for Telegram. Unknown kinds ignored."""
    fmt = FORMATTERS.get(str(kind))
    if fmt is None:
        return
    try:
        title, lines = fmt(data or {})
    except Exception as exc:  # noqa: BLE001
        print(f"[notify] telegram format failed for {kind}: {exc}", flush=True)
        return
    send_telegram(title, lines, kind=str(kind),
                  campaign_id=(data or {}).get("campaign_id"))


# --------------------------------------------------------------------------- #
# bus hook
# --------------------------------------------------------------------------- #
def hook_publish_event(server_module):
    """Wrap server_module.publish_event so every bus event also feeds Telegram.

    notify imports nothing from server at module level (no cycle); the server
    passes itself in because it may be running as __main__. The wrap is
    idempotent.
    """
    if getattr(server_module.publish_event, "_notify_wrapped", False):
        return
    original = server_module.publish_event

    def publish_event(kind, data=None):
        original(kind, data)
        try:
            handle_event(kind, data or {})
        except Exception:  # noqa: BLE001
            pass  # Telegram must never break the web bus

    publish_event._notify_wrapped = True
    publish_event.__name__ = "publish_event"
    server_module.publish_event = publish_event
