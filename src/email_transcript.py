"""Send generated transcripts via a dedicated SMTP mailbox (body only).

Credentials live in `.env` (SMTP_*). Recipients: TRANSCRIPT_RECIPIENT_EMAIL
plus TRANSCRIPT_FORWARD_EMAIL. The FULL transcript always goes in the email
body as numbered [S<id>] segments — never as an attachment — and a JSON reply
contract is included so an AI can reply with highlight segment ids. Failures
never abort the pipeline.
"""
import json
import smtplib
from email.mime.text import MIMEText
from pathlib import Path

from src.config import config
from src.clean_transcript import best_transcript_path
from src import email_status
from src import progress

EMAIL_RESULT_NOT_SENT = (False, [], "")


def _recipient_list(extra=None):
    raw = [
        config.transcript_recipient or "r73608925@gmail.com",
        config.transcript_forward or "syedmunavarahmed444@gmail.com",
    ]
    if extra:
        if isinstance(extra, str):
            raw.extend(extra.split(","))
        else:
            raw.extend(extra)
    seen = set()
    out = []
    for addr in raw:
        addr = (addr or "").strip()
        if not addr or addr.lower() in seen:
            continue
        seen.add(addr.lower())
        out.append(addr)
    return out


def _fmt_time(sec):
    try:
        sec = max(0, int(float(sec)))
    except (TypeError, ValueError):
        sec = 0
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


REPLY_CONTRACT = (
    "To return highlight picks, REPLY to this email and send ONLY this JSON "
    "(no other text), using the [S<id>] segment numbers from the transcript "
    "below (e.g. start_segment 12, end_segment 18 means the clip starts at "
    "[S12] and ends at [S18]; keep start/end within 12-120 seconds apart):\n"
    '{"video_id": "<repeat the VIDEO_ID above exactly>", "clips": '
    '[{"start_segment": <int>, "end_segment": <int>, '
    '"reason": "<short tag + why>", "score": <0.0-1.0>, '
    '"hook": "<punchy on-screen title, max 8 words>"}]}'
)


def build_transcript_body(data, video_id=None):
    video_id = video_id or data.get("video_id") or ""
    segments = data.get("segments") or []
    lines = []
    for i, seg in enumerate(segments):
        text = (seg.get("text") or "").strip()
        lines.append(f"[S{i}] ({_fmt_time(seg.get('start', 0))} - "
                     f"{_fmt_time(seg.get('end', 0))}) {text}")
    header = (
        f'ClipForge transcript for "{video_id}".\n\n'
        f"VIDEO_ID: {video_id}\n"
        f"DURATION: {data.get('duration')}s\n"
        f"SEGMENTS: {len(segments)}\n\n"
        f"{REPLY_CONTRACT}\n\n"
        "TRANSCRIPT (reference clips by their [S<id>] numbers):\n"
    )
    return header + "\n".join(lines) + "\n\nGenerated automatically by ClipForge."


def send_transcript_email(subject, body, recipient_email=None, video_id=None):
    """Returns (sent, recipients, error_message). Never raises."""
    smtp_host = config.smtp_host or "smtp.gmail.com"
    smtp_port = int(config.smtp_port or 587)
    sender_email = (config.smtp_user or "").strip()
    app_password = (config.smtp_pass or "").strip()
    recipients = _recipient_list(recipient_email)

    if not sender_email or not app_password:
        err = "SMTP_USER / SMTP_PASS not set"
        print(f"[email] {err}; skipping transcript email")
        return False, [], err
    if not recipients:
        err = "no transcript recipients configured"
        print(f"[email] {err}; skipping")
        return False, [], err

    msg = MIMEText(body or "", "plain", "utf-8")
    msg["From"] = f"ClipForge Transcripts <{sender_email}>"
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(sender_email, app_password)
            server.sendmail(sender_email, recipients, msg.as_string())
        print(f"[email] transcript sent to {', '.join(recipients)}"
              + (f" (video: {video_id})" if video_id else ""))
        return True, recipients, ""
    except Exception as exc:  # noqa: BLE001
        print(f"[email] failed to send transcript: {exc}")
        return False, recipients, str(exc)


def email_transcript_file(transcript_path, video_id=None,
                          recipients_dir=None):
    """Send one transcript file by email and record the send for the UI.

    recipients_dir is the candidates dir holding the video's
    _email_status.json (campaign dir or default). Returns True when sent.
    """
    transcript_path = Path(transcript_path)
    if not transcript_path.exists():
        print(f"[email] transcript not found: {transcript_path}")
        return False

    try:
        data = json.loads(transcript_path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        print(f"[email] could not read transcript: {exc}")
        return False

    video_id = video_id or data.get("video_id") or transcript_path.stem
    cleaned = bool(data.get("cleaned"))
    kind = "cleaned" if cleaned else "raw"
    subject = f"ClipForge transcript ({kind}): {video_id}"
    body = build_transcript_body(data, video_id=video_id)

    sent, recipients, err = send_transcript_email(subject, body,
                                                  video_id=video_id)
    if sent and recipients_dir is not None:
        email_status.mark_sent(recipients_dir, video_id, recipients,
                               kind=kind, subject=subject)
    progress.event("transcript_sent", {
        "video_id": video_id,
        "recipients": recipients,
        "kind": kind,
        "sent": bool(sent),
        "error": err,
    })
    return sent


def email_best_transcript(video_path, recipients_dir=None):
    """Send the best transcript for a video; `recipients_dir` (a
    candidates dir) stores the email-status record for the web UI."""
    path = best_transcript_path(video_path)
    return email_transcript_file(path, video_id=Path(video_path).stem,
                                 recipients_dir=recipients_dir)
