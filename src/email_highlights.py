"""Ingest AI highlight replies from the dedicated Gmail inbox.

Workflow:
1. ClipForge emails the numbered transcript (src/email_transcript.py) with a
   JSON reply contract.
2. The AI replies to r73608925@gmail.com with ONLY a JSON object listing
   highlight clips by segment id.
3. This module polls the inbox over IMAP, keeps only mail FROM
   HIGHLIGHT_REPLY_SENDER (default syedmunavarahmed444@gmail.com), parses the
   JSON, resolves segment ids against the stored transcript, and writes the
   same `<video_id>_candidates.json` that the local LLM would produce — so
   review, cut and render work unchanged.

Processed messages are marked read so they are never ingested twice.
"""
import email
import imaplib
import json
import re
from email.header import decode_header, make_header
from email.utils import parseaddr
from pathlib import Path

from src.config import config
from src import email_status


def _segments_with_ids(transcript):
    segments = []
    for i, seg in enumerate(transcript.get("segments", [])):
        seg = dict(seg)
        seg["id"] = i
        segments.append(seg)
    return segments


def _targets():
    """Yield (label, transcripts_dir, candidates_dir, Campaign|None)."""
    from src import campaigns as camp_mod
    targets = []
    for camp in camp_mod.list_campaigns():
        targets.append((f"campaign:{camp.id}", camp.transcripts_dir,
                        camp.candidates_dir, camp))
    targets.append(("default", config.transcripts_dir, config.candidates_dir, None))
    return targets


def _transcript_for(video_id):
    video_id = re.sub(r"[^\w.\- ]", "", str(video_id).strip())
    if not video_id:
        return None
    for label, tdir, cdir, camp in _targets():
        for name in (f"{video_id}_transcript_clean.json",
                     f"{video_id}_transcript.json"):
            p = tdir / name
            if p.exists():
                return label, p, cdir, camp
    return None


def ingest_highlight_payload(payload, source="email"):
    """Turn a parsed AI reply (dict) into a candidates file. Returns the path."""
    from src import select_highlights
    from src import campaigns as camp_mod

    if not isinstance(payload, dict):
        return None
    clips_raw = payload.get("clips")
    if not isinstance(clips_raw, list) or not clips_raw:
        print(f"[email] reply has no clips list; ignoring ({source})")
        return None
    video_id = str(payload.get("video_id") or "").strip()
    if not video_id:
        print("[email] reply missing video_id; ignoring")
        return None

    hit = _transcript_for(video_id)
    if not hit:
        print(f"[email] no transcript found for video_id={video_id!r}; ignoring reply")
        return None
    label, tpath, cdir, camp = hit

    transcript = json.loads(tpath.read_text(encoding="utf-8"))
    duration = float(transcript.get("duration", 0))
    segments = _segments_with_ids(transcript)
    if duration <= 0 or not segments:
        print(f"[email] transcript for {video_id} has no usable segments")
        return None

    settings = camp.settings() if camp else {}
    min_score = settings.get("min_score", config.llm_min_score)
    max_clips = settings.get("max_clips", config.llm_max_clips)

    clips = select_highlights._finalize(clips_raw, segments, duration,
                                        min_score, max_clips)
    clips = select_highlights._tighten(clips, segments, transcript.get("speech"))

    cdir.mkdir(parents=True, exist_ok=True)
    out_path = cdir / f"{video_id}_candidates.json"
    result = {
        "video_id": video_id,
        "source": str(tpath),
        "duration": duration,
        "model": "email",
        "highlights_from": source,
        "min_score": min_score,
        "clips": clips,
    }
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[email] ingested {len(clips)} highlight(s) for {video_id} "
          f"({label}) -> {out_path}")
    email_status.mark_received(cdir, video_id, len(clips), source=source)
    if camp is not None:
        camp_mod.sync_clips_from_candidates(camp, video_id, clips,
                                            status="reviewing")
        camp.touch()
    return out_path


def _payload_from_text(text):
    """Find the first balanced JSON object containing a clips list (tolerates
    quoted-reply noise around it)."""
    if not text:
        return None
    starts = []
    for key in ('"clips"', '"video_id"'):
        idx = text.find(key)
        while idx != -1:
            start = text.rfind("{", 0, idx)
            if start != -1 and start not in starts:
                starts.append(start)
            idx = text.find(key, idx + 1)
    for start in starts:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break
                    if isinstance(obj, dict) and isinstance(obj.get("clips"), list):
                        return obj
                    break
    return None


def _message_text(msg):
    parts = []
    for part in msg.walk():
        if part.get_content_type() != "text/plain":
            continue
        if "attachment" in str(part.get("Content-Disposition") or ""):
            continue
        raw = part.get_payload(decode=True)
        if not raw:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            parts.append(raw.decode(charset, errors="replace"))
        except LookupError:
            parts.append(raw.decode("utf-8", errors="replace"))
    return "\n".join(parts)


def _payload_from_attachments(msg):
    for part in msg.walk():
        fname = part.get_filename()
        if not fname or not fname.lower().endswith((".json", ".txt")):
            continue
        raw = part.get_payload(decode=True)
        if raw:
            hit = _payload_from_text(raw.decode("utf-8", errors="replace"))
            if hit:
                return hit
    return None


def _from_matches(msg, sender):
    _, addr = parseaddr(str(msg.get("From", "")))
    return addr.lower() == sender.lower()


def poll_highlight_emails(on_ingested=None):
    """One inbox pass. Returns ingestion summaries (one per ingested reply).

    on_ingested(summary) is called per successful ingest — the web server
    passes a callback that publishes UI notifications."""
    sender = (config.highlight_reply_sender or "").strip()
    user = (config.smtp_user or "").strip()
    password = (config.smtp_pass or "").strip()
    if not (sender and user and password):
        print("[email] highlight inbox polling not configured; skipping")
        return []

    summaries = []
    try:
        mail = imaplib.IMAP4_SSL(config.imap_host, config.imap_port)
        mail.login(user, password)
    except Exception as exc:  # noqa: BLE001
        print(f"[email] IMAP login failed: {exc}")
        return []

    try:
        mail.select(config.imap_folder or "INBOX")
        status, data = mail.search(None, "UNSEEN")
        ids = data[0].split() if status == "OK" and data[0] else []
        for num in ids[-25:]:
            status, parts = mail.fetch(num, "(RFC822)")
            if status != "OK" or not parts or not parts[0]:
                continue
            msg = email.message_from_bytes(parts[0][1])
            if not _from_matches(msg, sender):
                continue
            subject = str(make_header(decode_header(msg.get("Subject") or "")))
            payload = _payload_from_text(_message_text(msg))
            if payload is None:
                payload = _payload_from_attachments(msg)

            video_id = payload.get("video_id") if isinstance(payload, dict) else None
            if not video_id:
                m = re.search(r"ClipForge transcript \((?:cleaned|raw)\):\s*(.+)$",
                              subject)
                if m:
                    video_id = m.group(1).strip()
                    if isinstance(payload, dict) and not payload.get("video_id"):
                        payload["video_id"] = video_id

            if payload is not None:
                try:
                    out = ingest_highlight_payload(payload)
                except Exception as exc:  # noqa: BLE001
                    print(f"[email] failed to ingest reply: {exc}")
                    out = None
                if out:
                    try:
                        clip_count = len(json.loads(
                            out.read_text(encoding="utf-8")).get("clips") or [])
                    except Exception:  # noqa: BLE001
                        clip_count = len(payload.get("clips") or [])
                    summary = {
                        "video_id": str(payload.get("video_id") or "").strip(),
                        "clip_count": clip_count,
                        "subject": subject,
                    }
                    summaries.append(summary)
                    if on_ingested is not None:
                        try:
                            on_ingested(summary)
                        except Exception:  # noqa: BLE001
                            pass
            else:
                print(f"[email] unreadable highlight reply (subject: {subject!r}); "
                      "marked read without ingesting")
            mail.store(num, "+FLAGS", "\\Seen")
    except Exception as exc:  # noqa: BLE001
        print(f"[email] IMAP polling failed: {exc}")
    finally:
        try:
            mail.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            mail.logout()
        except Exception:  # noqa: BLE001
            pass
    return summaries
