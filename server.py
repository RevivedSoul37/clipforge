"""ClipForge web server (Starlette + uvicorn).

Serves the static frontend (web/) and a small REST API that runs the pipeline as
a subprocess and exposes live logs + progress. Run with: python server.py
"""
import json
import os
import subprocess
import sys
import threading
import uuid
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

from src.config import config  # noqa: E402
from src import campaigns as camp_mod  # noqa: E402

WEB_DIR = ROOT / "web"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
MAX_LOGS = 4000

RUNS = {}          # run_id -> dict(status, logs, percent, stage, message, command, exit_code)
RUNS_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
# run engine
# --------------------------------------------------------------------------- #
def _error_from_logs(logs):
    """Pick the most informative recent line instead of blindly the last one."""
    markers = ("error", "traceback", "failed", "exception", "not found", "not recognized")
    for line in reversed(logs[-40:]):
        low = line.lower()
        if low.strip() and any(m in low for m in markers):
            return line.strip()
    for line in reversed(logs):
        if line.strip():
            return line.strip()
    return "pipeline failed"


def _run_subprocess(run_id, argv):
    cmd = [sys.executable, "-u", str(ROOT / "main.py"), "--emit-progress"] + argv
    with RUNS_LOCK:
        run = RUNS[run_id]
        run["status"] = "running"
        run["command"] = " ".join(cmd)

    print(f"\n>>> {run['command']}", flush=True)

    popen_kwargs = dict(
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
    )
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)
    with RUNS_LOCK:
        run["proc"] = proc

    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        if line.startswith("@@PROGRESS@@"):
            try:
                data = json.loads(line.split(" ", 1)[1])
            except Exception:  # noqa: BLE001
                continue
            with RUNS_LOCK:
                run["percent"] = data.get("percent")
                run["stage"] = data.get("stage")
                run["message"] = data.get("message", "")
            # mirror to the backend console as a compact status line
            print(f"   [{data.get('percent', 0):>5.1f}%] {data.get('stage')} - {data.get('message', '')}", flush=True)
        else:
            with RUNS_LOCK:
                run["logs"].append(line)
                run["log_count"] += 1
                if len(run["logs"]) > MAX_LOGS:
                    excess = len(run["logs"]) - MAX_LOGS
                    del run["logs"][:excess]
                    run["log_offset"] += excess
            print(line, flush=True)

    proc.wait()
    with RUNS_LOCK:
        run["exit_code"] = proc.returncode
        if run.get("cancelled"):
            run["status"] = "cancelled"
            run["error"] = "Cancelled by user."
        elif proc.returncode == 0:
            run["status"] = "ok"
            run["percent"] = 100
        else:
            run["status"] = "error"
            run["error"] = _error_from_logs(run["logs"])
    print(f"\n<<< run {run_id} finished ({run['status']})\n", flush=True)


def start_run(argv):
    run_id = uuid.uuid4().hex[:12]
    with RUNS_LOCK:
        RUNS[run_id] = {
            "status": "queued", "logs": [], "log_offset": 0, "log_count": 0,
            "percent": 0, "stage": "start", "message": "Starting",
            "command": "", "exit_code": None, "error": None,
            "cancelled": False, "proc": None,
        }
    threading.Thread(target=_run_subprocess, args=(run_id, argv), daemon=True).start()
    return run_id


def _kill_tree(proc):
    """Terminate a process and its children (ffmpeg runs as a grandchild)."""
    if proc is None or proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=10)
        else:
            import signal
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (AttributeError, ProcessLookupError):
                proc.terminate()
    except Exception:  # noqa: BLE001
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass


def cancel_run(run_id):
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            return False
        run["cancelled"] = True
        proc = run.get("proc")
    _kill_tree(proc)
    return True


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _cid(request, extra=None):
    cid = request.path_params.get("campaign_id") or request.query_params.get("campaign_id")
    if not cid and extra:
        cid = extra.get("campaign_id")
    return cid or None


def _camp(campaign_id):
    return camp_mod.get_campaign(campaign_id) if campaign_id else None


def _input_dir(campaign_id=None):
    return config.input_dir_for(campaign_id)


def _output_dir(campaign_id=None):
    return config.output_dir_for(campaign_id)


def _raw_dir(campaign_id=None):
    return config.raw_dir_for(campaign_id)


def _candidates_dir(campaign_id=None):
    if campaign_id:
        return config.campaign_root(campaign_id) / "clip_candidates"
    return config.candidates_dir


def _frames_dir(campaign_id=None):
    if campaign_id:
        return config.campaign_root(campaign_id) / "frames"
    return config.frames_dir


def _transcripts_dir(campaign_id=None):
    if campaign_id:
        return config.campaign_root(campaign_id) / "transcripts"
    return config.transcripts_dir


def _list_videos(campaign_id=None):
    folder = _input_dir(campaign_id)
    folder.mkdir(parents=True, exist_ok=True)
    vids = []
    for p in sorted(folder.iterdir()):
        if p.suffix.lower() in VIDEO_EXTS:
            vids.append({"name": p.name, "id": p.stem, "size": p.stat().st_size})
    return vids


def _list_templates(campaign_id=None):
    tdir = ROOT / "templates"
    out = []
    camp = _camp(campaign_id)
    if camp and camp.has_template():
        d = _read_json(camp.template_path) or {}
        out.append({
            "name": str(camp.template_path),
            "label": d.get("name") or "Campaign style",
            "description": d.get("description", "Style Lab draft for this campaign"),
            "file": "template.json",
        })
    for p in sorted(tdir.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "name": d.get("name", p.stem),
                "label": d.get("name", p.stem),
                "description": d.get("description", ""),
                "file": p.name,
            })
        except Exception:  # noqa: BLE001
            continue
    return out


def _read_json(p: Path):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _candidates_for(video_id, campaign_id=None):
    return _read_json(_candidates_dir(campaign_id) / f"{video_id}_candidates.json")


def _transcript_segments(video_id, campaign_id=None):
    from src.clean_transcript import best_transcript_path
    data = _read_json(best_transcript_path(video_id, transcripts_dir=_transcripts_dir(campaign_id)))
    if not data:
        return []
    return [{"start": s["start"], "end": s["end"], "text": s.get("text", "")}
            for s in data.get("segments", [])]


def _media_list(video_id, base_dir, prefix=""):
    base_dir.mkdir(parents=True, exist_ok=True)
    key = prefix or video_id
    out = []
    for p in sorted(base_dir.iterdir()):
        if p.suffix.lower() not in VIDEO_EXTS:
            continue
        if p.stem == key or p.stem.startswith(key + "_"):
            out.append({"name": p.name, "size": p.stat().st_size,
                        "url": f"/api/media?path={p.relative_to(ROOT).as_posix()}"})
    return out


def _safe_resolve(rel):
    """Resolve a web-relative path and refuse anything escaping ROOT."""
    try:
        p = (ROOT / rel).resolve()
        p.relative_to(ROOT.resolve())
    except (ValueError, OSError):
        return None
    return p if p.is_file() else None


def _resolve_video_arg(video, campaign_id=None):
    """Accept a stem ('sample3'), filename, or path; return an absolute path."""
    p = Path(video)
    folder = _input_dir(campaign_id)
    if p.is_absolute() and p.exists():
        return str(p)
    if p.suffix:
        candidate = folder / p.name
        if candidate.exists():
            return str(candidate)
        return str(p)
    if folder.is_dir():
        for f in sorted(folder.iterdir()):
            if f.stem == video and f.suffix.lower() in VIDEO_EXTS:
                return str(f)
    return str(p)


# --------------------------------------------------------------------------- #
# routes
# --------------------------------------------------------------------------- #
async def index(request):
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-store"})


async def api_state(request):
    campaign_id = _cid(request)
    camp = _camp(campaign_id)
    default_tpl = str(camp.template_path) if camp and camp.has_template() else config.default_template
    return JSONResponse({
        "videos": _list_videos(campaign_id),
        "templates": _list_templates(campaign_id),
        "campaign_id": campaign_id,
        "config": {
            "llm_model": config.llm_model,
            "whisper_model": config.whisper_model,
            "min_score": config.llm_min_score,
            "max_clips": config.llm_max_clips,
            "default_template": default_tpl,
            "input_dir": str(_input_dir(campaign_id)),
            "output_dir": str(_output_dir(campaign_id)),
        },
    })


async def api_video(request):
    video_id = request.path_params["video_id"]
    campaign_id = _cid(request)
    candidates = _candidates_for(video_id, campaign_id)
    return JSONResponse({
        "candidates": candidates,
        "transcript_segments": _transcript_segments(video_id, campaign_id),
        "outputs": _media_list(video_id, _output_dir(campaign_id)),
        "raws": _media_list(video_id, _raw_dir(campaign_id)),
    })


async def api_video_delete(request):
    """Delete a source video from input/ (keeps transcripts/candidates/outputs
    so already-rendered clips stay usable). Refuses while a run is active."""
    video_id = request.path_params["video_id"]
    with RUNS_LOCK:
        busy = any(r["status"] in ("running", "queued") for r in RUNS.values())
    if busy:
        return JSONResponse(
            {"error": "A pipeline run is in progress - cancel it before deleting a source video."},
            status_code=409)
    campaign_id = _cid(request)
    folder = _input_dir(campaign_id)
    if not folder.is_dir():
        return JSONResponse({"error": "input/ folder missing"}, status_code=404)
    src = None
    for f in sorted(folder.iterdir()):
        if f.is_file() and f.stem == video_id and f.suffix.lower() in VIDEO_EXTS:
            src = f
            break
    if src is None:
        return JSONResponse({"error": f"no source video '{video_id}' in input/"},
                            status_code=404)
    try:
        src.unlink()
    except OSError as exc:
        return JSONResponse({"error": f"could not delete {src.name}: {exc}"},
                            status_code=500)
    print(f"[delete] removed source video {src.name}", flush=True)
    return JSONResponse({"ok": True, "name": src.name})


async def api_run(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    mode = data.get("mode", "pipeline")
    video = data.get("video")
    campaign_id = data.get("campaign_id")
    if not video:
        return JSONResponse({"error": "missing 'video'"}, status_code=400)

    if mode not in ("analyze", "export", "pipeline", "transcribe", "context",
                    "select", "cut", "render", "broll", "frames", "style"):
        return JSONResponse({"error": f"unknown mode {mode}"}, status_code=400)

    argv = [mode, _resolve_video_arg(video, campaign_id)]
    if campaign_id:
        argv = ["--campaign", str(campaign_id)] + argv
    template = data.get("template")
    min_score = data.get("min_score")
    max_clips = data.get("max_clips")
    auto = data.get("auto")

    if mode == "analyze":
        if min_score is not None:
            argv += ["--min-score", str(min_score)]
        if max_clips is not None:
            argv += ["--max-clips", str(max_clips)]
    elif mode == "export":
        if template:
            argv += ["--template", template]
        if auto:
            argv += ["--auto"]
    elif mode == "pipeline":
        if template:
            argv += ["--template", template]
        if min_score is not None:
            argv += ["--min-score", str(min_score)]
        if max_clips is not None:
            argv += ["--max-clips", str(max_clips)]
        if auto:
            argv += ["--auto"]
        argv += ["--skip-review"]
    elif mode == "render":
        if template:
            argv += ["--template", template]
        if auto:
            argv += ["--auto"]
    elif mode == "select":
        if min_score is not None:
            argv += ["--min-score", str(min_score)]
        if max_clips is not None:
            argv += ["--max-clips", str(max_clips)]
    elif mode == "broll":
        argv += ["--fetch"]
    elif mode == "frames":
        if data.get("frames_mode"):
            argv += ["--mode", str(data["frames_mode"])]
        if data.get("num") is not None:
            argv += ["--num", str(int(data["num"]))]
        if data.get("grid"):
            argv += ["--grid", str(data["grid"])]
    elif mode == "style":
        if data.get("name"):
            argv += ["--name", str(data["name"])]
        if data.get("cta_text"):
            argv += ["--cta-text", str(data["cta_text"])]
    elif mode == "cut":
        if auto:
            argv += ["--auto"]
    # transcribe / context take no extra args from the UI

    run_id = start_run(argv)
    return JSONResponse({"run": run_id})


async def api_run_status(request):
    run_id = request.path_params["run_id"]
    try:
        since = max(0, int(request.query_params.get("since", "0") or 0))
    except ValueError:
        since = 0
    with RUNS_LOCK:
        run = RUNS.get(run_id)
        if not run:
            return JSONResponse({"error": "no such run"}, status_code=404)
        logs, offset, total = run["logs"], run["log_offset"], run["log_count"]
        # client asks for absolute index `since`; clamp into the retained window
        idx = max(since, offset)
        fresh = logs[idx - offset:]
        dropped = max(0, idx - since)
        return JSONResponse({
            "run": run_id,
            "status": run["status"],
            "percent": run["percent"],
            "stage": run["stage"],
            "message": run["message"],
            "command": run["command"],
            "error": run["error"],
            "logs": fresh,
            "log_index": idx + len(fresh),
            "log_dropped": dropped,
            "log_total": total,
        })


async def api_run_cancel(request):
    run_id = request.path_params["run_id"]
    ok = cancel_run(run_id)
    if not ok:
        return JSONResponse({"error": "no such run"}, status_code=404)
    return JSONResponse({"ok": True})


async def api_save_candidates(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    video_id = data.get("video_id")
    clips = data.get("clips")
    if not video_id or not isinstance(clips, list):
        return JSONResponse({"error": "need video_id and clips[]"}, status_code=400)

    campaign_id = data.get("campaign_id")
    p = _candidates_dir(campaign_id) / f"{video_id}_candidates.json"
    if not p.exists():
        return JSONResponse({"error": "no candidates file"}, status_code=404)

    cur = _read_json(p)
    cleaned = []
    for c in clips:
        if not all(k in c for k in ("start", "end", "score", "reason")):
            continue
        item = {
            "start": round(float(c["start"]), 3),
            "end": round(float(c["end"]), 3),
            "score": round(float(c["score"]), 3),
            "reason": str(c.get("reason", "")),
            "hook": str(c.get("hook", "") or ""),
            "status": c.get("status", "pending"),
        }
        # preserve pipeline metadata when present
        for key in ("start_segment", "end_segment"):
            if isinstance(c.get(key), int):
                item[key] = c[key]
        if isinstance(c.get("broll"), list):
            item["broll"] = c["broll"]
        cleaned.append(item)
    cur["clips"] = cleaned
    p.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse({"ok": True, "count": len(cleaned)})


async def api_broll(request):
    from src.fetch_broll import VALID_EMOTIONS, VIDEO_EXTS, IMAGE_EXTS
    emotions = {}
    for e in VALID_EMOTIONS:
        d = config.broll_dir / e
        n = 0
        if d.is_dir():
            n = sum(1 for p in d.iterdir()
                    if p.is_file() and p.suffix.lower() in VIDEO_EXTS + IMAGE_EXTS)
        emotions[e] = n
    return JSONResponse({
        "emotions": emotions,
        "total": sum(emotions.values()),
        "providers": {
            "pexels": bool(config.broll_pexels_key),
            "pixabay": bool(config.broll_pixabay_key),
        },
    })


async def api_rules_get(request):
    try:
        text = config.rules_file.read_text(encoding="utf-8")
    except OSError:
        text = ""
    return JSONResponse({"rules": text, "path": str(config.rules_file)})


async def api_rules_save(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    rules = data.get("rules")
    if rules is None or not isinstance(rules, str):
        return JSONResponse({"error": "need 'rules' string"}, status_code=400)
    config.rules_file.parent.mkdir(parents=True, exist_ok=True)
    config.rules_file.write_text(rules, encoding="utf-8")
    return JSONResponse({"ok": True})


def _music_tracks():
    from src.apply_template import AUDIO_EXTS
    config.music_dir.mkdir(parents=True, exist_ok=True)
    return sorted(p.name for p in config.music_dir.iterdir()
                  if p.is_file() and p.suffix.lower() in AUDIO_EXTS)


def _read_template(name):
    from src.apply_template import load_template
    try:
        return load_template(name)
    except FileNotFoundError:
        return None


async def api_music_get(request):
    t = _read_template(config.default_template) or {}
    mus = t.get("music", {}) or {}
    return JSONResponse({
        "enabled": bool(mus.get("enabled")),
        "volume": float(mus.get("volume", 0.12)),
        "track": mus.get("track", "") or "",
        "tracks": _music_tracks(),
    })


async def api_music_save(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    t = _read_template(config.default_template)
    if t is None:
        return JSONResponse({"error": "default template not found"}, status_code=404)
    mus = t.setdefault("music", {})
    if "enabled" in data:
        mus["enabled"] = bool(data["enabled"])
    if "volume" in data:
        try:
            mus["volume"] = min(1.0, max(0.0, float(data["volume"])))
        except (TypeError, ValueError):
            return JSONResponse({"error": "volume must be a number 0-1"}, status_code=400)
    if "track" in data:
        mus["track"] = str(data["track"] or "")
    path = (config.root / "templates" / config.default_template).with_suffix(".json")
    path.write_text(json.dumps(t, ensure_ascii=False, indent=2), encoding="utf-8")
    return JSONResponse({"ok": True})


async def api_music_upload(request):
    form = await request.form()
    upload = form.get("file")
    if not upload or not getattr(upload, "filename", None):
        return JSONResponse({"error": "no file uploaded"}, status_code=400)
    from src.apply_template import AUDIO_EXTS
    raw_name = Path(upload.filename).name.strip()
    if Path(raw_name).suffix.lower() not in AUDIO_EXTS:
        return JSONResponse({"error": f"unsupported audio type: {raw_name}"}, status_code=400)
    config.music_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(config.music_dir, raw_name)
    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            size += len(chunk)
    if size == 0:
        dest.unlink(missing_ok=True)
        return JSONResponse({"error": "uploaded file was empty"}, status_code=400)
    return JSONResponse({"name": dest.name, "size": size})


def _unique_dest(directory, filename):
    """Return a non-colliding destination path (append a counter before ext)."""
    stem, suffix = Path(filename).stem, Path(filename).suffix
    candidate = directory / filename
    n = 1
    while candidate.exists():
        candidate = directory / f"{stem}_{n}{suffix}"
        n += 1
    return candidate


async def api_upload(request):
    form = await request.form()
    upload = form.get("file")
    if not upload or not getattr(upload, "filename", None):
        return JSONResponse({"error": "no file uploaded"}, status_code=400)

    raw_name = Path(upload.filename).name.strip()
    if not raw_name or raw_name in (".", ".."):
        return JSONResponse({"error": "invalid filename"}, status_code=400)
    if Path(raw_name).suffix.lower() not in VIDEO_EXTS:
        return JSONResponse({"error": f"unsupported file type: {raw_name}"}, status_code=400)

    campaign_id = form.get("campaign_id")
    folder = _input_dir(campaign_id)
    folder.mkdir(parents=True, exist_ok=True)
    dest = _unique_dest(folder, raw_name)

    size = 0
    with open(dest, "wb") as f:
        while True:
            chunk = await upload.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            size += len(chunk)

    if size == 0:
        dest.unlink(missing_ok=True)
        return JSONResponse({"error": "uploaded file was empty"}, status_code=400)

    print(f"[upload] {raw_name} -> {dest} ({size / 1048576:.1f} MB)", flush=True)
    return JSONResponse({"name": dest.name, "id": dest.stem, "size": size})


async def api_media(request):
    rel = request.query_params.get("path", "")
    p = _safe_resolve(rel)
    if p is None or not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)


async def api_preview(request):
    import anyio
    from src.cut_clips import cut_one

    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)

    video = data.get("video")
    try:
        start, end = float(data.get("start")), float(data.get("end"))
    except (TypeError, ValueError):
        return JSONResponse({"error": "need numeric start and end"}, status_code=400)
    if not video or start < 0 or end <= start:
        return JSONResponse({"error": "need video + valid start < end"}, status_code=400)

    campaign_id = data.get("campaign_id")
    src = Path(_resolve_video_arg(video, campaign_id))
    if not src.exists():
        return JSONResponse({"error": f"source video not found: {src}"}, status_code=404)

    raw = _raw_dir(campaign_id)
    raw.mkdir(parents=True, exist_ok=True)
    dest = raw / f"preview_{src.stem}_{int(start)}-{int(end)}.mp4"
    # drop older previews of this video to avoid unlimited raw/ growth
    for old in raw.glob(f"preview_{src.stem}_*.mp4"):
        old.unlink(missing_ok=True)

    try:
        def do_cut():
            return cut_one(src, start, end, dest, lead_in=0.0, lead_out=0.0)

        await anyio.to_thread.run_sync(do_cut)
    except Exception as exc:  # noqa: BLE001
        dest.unlink(missing_ok=True)
        return JSONResponse({"error": str(exc)[-500:]}, status_code=500)

    return JSONResponse({
        "url": f"/api/media?path={dest.relative_to(ROOT).as_posix()}",
    })


async def api_frames_list(request):
    """List extracted frame sets with their sheets/reports."""
    campaign_id = _cid(request)
    fdir = _frames_dir(campaign_id)
    fdir.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(fdir.iterdir()):
        if not d.is_dir():
            continue
        man = _read_json(d / "manifest.json") or {}
        has_report = (d / "style_report.json").exists()
        frames = [f["file"] for f in man.get("frames", [])]
        out.append({
            "stem": d.name,
            "frames": len(frames),
            "sheets": man.get("sheets", []),
            "has_report": has_report,
            "mode": man.get("mode"),
        })
    return JSONResponse({"frame_sets": out})


async def api_style_report(request):
    """Return the stored style report + draft template for a stem."""
    stem = request.path_params["stem"]
    campaign_id = _cid(request)
    d = _frames_dir(campaign_id) / stem
    report = _read_json(d / "style_report.json")
    if report is None:
        return JSONResponse({"error": f"no style report for '{stem}'"}, status_code=404)
    camp = _camp(campaign_id)
    if camp and camp.has_template():
        tpl = _read_json(camp.template_path)
        tpl_name = "template.json"
    else:
        tpl_name = f"{stem}_style" if not stem.endswith("_style") else stem
        tpl = _read_json(config.root / "templates" / f"{tpl_name}.json")
    return JSONResponse({
        "report": report,
        "template": tpl,
        "template_name": tpl_name,
    })


async def api_frames_media(request):
    """Serve a frame image or contact sheet from data/frames/."""
    stem = request.path_params["stem"]
    name = request.query_params.get("file", "")
    if "/" in name or chr(92) in name or ".." in name:
        return JSONResponse({"error": "bad file name"}, status_code=400)
    campaign_id = _cid(request)
    p = _frames_dir(campaign_id) / stem / name
    if not p.is_file():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p)


# --------------------------------------------------------------------------- #
# campaigns
# --------------------------------------------------------------------------- #
async def api_campaigns_list(request):
    return JSONResponse({
        "campaigns": [c.public() for c in camp_mod.list_campaigns()],
    })


async def api_campaigns_create(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    try:
        camp = camp_mod.create_campaign(
            data.get("name"),
            platform=data.get("platform") or "",
            payout_rate=data.get("payout_rate") or "",
            deadline=data.get("deadline") or "",
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(camp.public(detail=True), status_code=201)


async def api_campaign_get(request):
    camp = _camp(request.path_params["campaign_id"])
    if camp is None:
        return JSONResponse({"error": "campaign not found"}, status_code=404)
    return JSONResponse(camp.public(detail=True))


async def api_campaign_patch(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    try:
        camp = camp_mod.update_campaign(request.path_params["campaign_id"], data)
    except FileNotFoundError:
        return JSONResponse({"error": "campaign not found"}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse(camp.public(detail=True))


async def api_campaign_rules_upload(request):
    campaign_id = request.path_params["campaign_id"]
    camp = _camp(campaign_id)
    if camp is None:
        return JSONResponse({"error": "campaign not found"}, status_code=404)
    form = await request.form()
    upload = form.get("file")
    if not upload or not getattr(upload, "filename", None):
        return JSONResponse({"error": "no file uploaded"}, status_code=400)
    raw_name = Path(upload.filename).name.strip()
    data = await upload.read()
    if not data:
        return JSONResponse({"error": "uploaded file was empty"}, status_code=400)
    try:
        dest = camp_mod.save_rules_upload(camp, raw_name, data)
        extracted = camp_mod.extract_rules_text(dest)
        summary = camp_mod.summarize_rules(extracted)
        camp.write_rules_summary(summary)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    except RuntimeError as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)
    return JSONResponse({
        "ok": True,
        "rules_summary": summary,
        "rules_full": dest.name,
    })


async def api_campaign_rules_patch(request):
    camp = _camp(request.path_params["campaign_id"])
    if camp is None:
        return JSONResponse({"error": "campaign not found"}, status_code=404)
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    current = camp.rules_summary()
    if "section" in data:
        try:
            updated = camp_mod.patch_rules_section(
                current, data.get("section"), data.get("value"))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
    elif isinstance(data.get("rules_summary"), dict):
        updated = camp_mod.normalize_rules(data["rules_summary"])
        updated["submission_done"] = current.get("submission_done", False)
        if "submission_done" in data["rules_summary"]:
            updated["submission_done"] = bool(data["rules_summary"]["submission_done"])
    else:
        return JSONResponse(
            {"error": "need {section, value} or rules_summary object"},
            status_code=400)
    camp.write_rules_summary(updated)
    return JSONResponse({"ok": True, "rules_summary": updated})


async def api_campaign_rules_file(request):
    camp = _camp(request.path_params["campaign_id"])
    if camp is None:
        return JSONResponse({"error": "campaign not found"}, status_code=404)
    path = camp.rules_full_path()
    if path is None:
        return JSONResponse({"error": "no rules document uploaded"}, status_code=404)
    return FileResponse(path, filename=path.name)


async def api_campaign_clip_patch(request):
    try:
        data = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"error": "invalid JSON body"}, status_code=400)
    status = data.get("status")
    try:
        clip = camp_mod.update_clip_status(
            request.path_params["campaign_id"],
            request.path_params["clip_id"],
            status,
        )
    except FileNotFoundError as exc:
        return JSONResponse({"error": str(exc)}, status_code=404)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    return JSONResponse({"ok": True, "clip": clip})


async def api_open_folder(request):
    directory = request.query_params.get("dir", "output")
    campaign_id = _cid(request)
    if directory == "input":
        target = _input_dir(campaign_id)
    else:
        target = _output_dir(campaign_id)
    target.mkdir(parents=True, exist_ok=True)
    try:
        if sys.platform == "win32":
            os.startfile(str(target))  # noqa: S606
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(target)])
        else:
            subprocess.Popen(["xdg-open", str(target)])
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"could not open folder: {exc}"}, status_code=500)
    return JSONResponse({"ok": True, "dir": str(target)})


routes = [
    Route("/", index),
    Route("/api/state", api_state, methods=["GET"]),
    Route("/api/video/{video_id}", api_video, methods=["GET"]),
    Route("/api/video/{video_id}", api_video_delete, methods=["POST"]),
    Route("/api/run", api_run, methods=["POST"]),
    Route("/api/run/{run_id}", api_run_status, methods=["GET"]),
    Route("/api/run/{run_id}/cancel", api_run_cancel, methods=["POST"]),
    Route("/api/candidates", api_save_candidates, methods=["POST"]),
    Route("/api/rules", api_rules_get, methods=["GET"]),
    Route("/api/rules", api_rules_save, methods=["POST"]),
    Route("/api/music", api_music_get, methods=["GET"]),
    Route("/api/music", api_music_save, methods=["POST"]),
    Route("/api/music/upload", api_music_upload, methods=["POST"]),
    Route("/api/broll", api_broll, methods=["GET"]),
    Route("/api/upload", api_upload, methods=["POST"]),
    Route("/api/media", api_media, methods=["GET"]),
    Route("/api/preview", api_preview, methods=["POST"]),
    Route("/api/frames", api_frames_list, methods=["GET"]),
    Route("/api/frames/{stem}/style", api_style_report, methods=["GET"]),
    Route("/api/frames/{stem}/media", api_frames_media, methods=["GET"]),
    Route("/api/open-folder", api_open_folder, methods=["POST"]),
    Route("/api/campaigns", api_campaigns_list, methods=["GET"]),
    Route("/api/campaigns", api_campaigns_create, methods=["POST"]),
    Route("/api/campaigns/{campaign_id}", api_campaign_get, methods=["GET"]),
    Route("/api/campaigns/{campaign_id}", api_campaign_patch, methods=["PATCH"]),
    Route("/api/campaigns/{campaign_id}/rules", api_campaign_rules_upload, methods=["POST"]),
    Route("/api/campaigns/{campaign_id}/rules", api_campaign_rules_patch, methods=["PATCH"]),
    Route("/api/campaigns/{campaign_id}/rules/file", api_campaign_rules_file, methods=["GET"]),
    Route("/api/campaigns/{campaign_id}/clips/{clip_id}", api_campaign_clip_patch, methods=["PATCH"]),
    Mount("/static", app=StaticFiles(directory=str(WEB_DIR)), name="static"),
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    import uvicorn
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8600
    config.ensure_dirs()
    print("=" * 60)
    print("  ClipForge - AI auto-clipper")
    print(f"  Web UI:  http://localhost:{port}")
    print(f"  Input:   {config.input_dir}")
    print(f"  Output:  {config.output_dir}")
    print("  Backend logs stream below (also shown in the web UI).")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
