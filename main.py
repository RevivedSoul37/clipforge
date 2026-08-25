"""ClipForge â€” pipeline orchestrator.

Stages: input â†’ transcribe â†’ context â†’ select highlights â†’ review â†’ cut â†’ auto-edit â†’ output.

Commands:
  analyze     transcribe â†’ context â†’ select (produces candidates for review)
  export      cut + render approved clips
  pipeline    full auto run (analyze + cut + render), review skipped
  transcribe / context / select / cut / render   single stages
  review      legacy Streamlit review UI
  batch       process a whole folder

When --emit-progress is passed (before the command), each stage emits
@@PROGRESS@@ JSON lines on stdout for the web UI to parse.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

for _stream in ("stdout", "stderr"):
    _s = getattr(sys, _stream, None)
    if _s is not None and hasattr(_s, "reconfigure"):
        try:
            _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

from src.config import config, ROOT
from src import transcribe, build_context, select_highlights, cut_clips, apply_template
from src import clean_transcript
from src.email_transcript import email_best_transcript
from src import email_highlights

from src import extract_frames, analyze_frames
from src import progress
from src import campaigns as camp_mod
from src import style_explorer

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")


def _resolve_video(value):
    p = Path(value)
    if p.is_absolute():
        if not p.exists():
            raise FileNotFoundError(f"Video not found: {p}")
        return p
    if p.exists():
        return p
    if config.input_dir.is_dir():
        candidate = config.input_dir / p.name
        if candidate.exists():
            return candidate
        # bare stem (e.g. "sample3") -> match any video file with that stem
        for f in sorted(config.input_dir.iterdir()):
            if f.is_file() and f.stem.lower() == p.name.lower() \
                    and f.suffix.lower() in VIDEO_EXTS:
                return f
    raise FileNotFoundError(
        f"Video not found: {p} (looked in cwd and {config.input_dir})")


def _active_campaign():
    cid = getattr(config, "active_campaign_id", None)
    return camp_mod.get_campaign(cid) if cid else None


def _sync_reviewing(video):
    camp = _active_campaign()
    if not camp:
        return
    data = {}
    cp = _candidates_path(video)
    if cp.exists():
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            data = {}
    camp_mod.sync_clips_from_candidates(camp, video.stem, data.get("clips") or [],
                                        status="reviewing")


def _sync_exported(video, clips=None):
    camp = _active_campaign()
    if not camp:
        return
    camp_mod.mark_clips_exported(camp, video.stem, clips)
    camp.touch()


def _transcript_path(video):
    return clean_transcript.best_transcript_path(video)


def _context_path(video):
    return config.context_dir / f"{video.stem}_context.json"


def _candidates_path(video):
    return config.candidates_dir / f"{video.stem}_candidates.json"


def _approved(candidates_path, auto=False):
    if not candidates_path.exists():
        raise FileNotFoundError(f"Candidates not found: {candidates_path}")
    data = json.loads(candidates_path.read_text(encoding="utf-8"))
    clips = data["clips"]
    if auto:
        clips = [c for c in clips if c.get("score", 0.0) >= data.get("min_score", config.llm_min_score)]
        for c in clips:
            c["status"] = "approved"
        try:
            candidates_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
    else:
        clips = [c for c in clips if c.get("status") == "approved"]
    return data, clips


def _scaled(start, end, stage):
    def cb(frac):
        frac = min(1.0, max(0.0, float(frac)))
        progress.emit(start + (end - start) * frac, stage)
    return cb


def _use_local_highlights(args, camp):
    """True = run the local Ollama select stage; False = email the transcript
    and ingest highlights from the AI reply (src/email_highlights.py)."""
    if getattr(args, "no_local_highlights", False):
        return False
    if camp is not None:
        return bool(camp.settings().get("local_highlights", True))
    return config.local_highlights


def _email_gate(video, camp):
    """Email mode: write an empty candidates placeholder so review/approval
    shows a waiting state instead of crashing. The server's inbox poller (or
    `check-email`) fills it in when the highlight reply arrives."""
    settings = camp.settings() if camp else {}
    min_score = settings.get("min_score", config.llm_min_score)
    path = _candidates_path(video)
    transcript_path = _transcript_path(video)
    duration = 0.0
    try:
        duration = float(json.loads(
            transcript_path.read_text(encoding="utf-8")).get("duration", 0))
    except Exception:  # noqa: BLE001
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "video_id": video.stem,
        "source": str(video),
        "duration": duration,
        "model": "email",
        "highlights_from": "pending",
        "min_score": min_score,
        "clips": [],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _email_await(video, camp, message=""):
    """Send the transcript email (best effort), write the awaiting placeholder
    and tell the UI. The run finishes immediately; the reply is picked up by
    the web server's background inbox poller."""
    sent = False
    try:
        sent = email_best_transcript(video,
                                     recipients_dir=config.candidates_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[email] skipped: {exc}")
    _email_gate(video, camp)
    _sync_reviewing(video)
    progress.emit(100, "awaiting", message or
                  ("Transcript emailed — waiting for the highlight reply"
                   if sent else "Transcript email failed — check email settings"))
    progress.event("awaiting_highlights", {
        "video_id": video.stem,
        "sent": bool(sent),
    })
    return sent


def cmd_transcribe(args):
    if args.emit_progress:
        progress.enable()
    progress.emit(0, "transcribe", "Loading model and extracting audio")
    video = _resolve_video(args.video)
    transcribe.transcribe(video, model_size=args.model,
                          device=args.device, compute_type=args.compute,
                          language=args.language,
                          progress=_scaled(0, 100, "transcribe"))
    if not getattr(args, "skip_email", False):
        try:
            email_best_transcript(video, recipients_dir=config.candidates_dir)
        except Exception as exc:  # noqa: BLE001
            print(f"[email] skipped: {exc}")
    progress.emit(100, "done", "Transcript saved")


def cmd_clean(args):
    if args.emit_progress:
        progress.enable()
    progress.emit(0, "clean", "Fixing low-confidence words")
    video = _resolve_video(args.video)
    clean_transcript.clean_transcript(video, transcript_path=args.transcript)
    try:
        email_best_transcript(video, recipients_dir=config.candidates_dir)
    except Exception as exc:  # noqa: BLE001
        print(f"[email] skipped: {exc}")
    progress.emit(100, "done", "Transcript cleaned")


def cmd_context(args):
    if args.emit_progress:
        progress.enable()
    progress.emit(0, "context", "Building video context")
    build_context.build_context(_resolve_video(args.video), transcript_path=args.transcript)
    progress.emit(100, "done", "Context saved")


def cmd_select(args):
    if args.emit_progress:
        progress.enable()
    video = _resolve_video(args.video)
    rules = None
    camp = _active_campaign()
    if camp:
        rules = camp.rules_summary() if camp else None
        camp_mod.add_analyzing_placeholder(camp, video.stem)
    force_local = getattr(args, "local", False)
    if getattr(args, "email", False) or (not force_local and not _use_local_highlights(args, camp)):
        _email_await(video, camp)
        return
    context_path = Path(args.context) if args.context else _context_path(video)
    needed_context = not context_path.exists()
    if needed_context:
        print("[select] no context yet — building it from the existing transcript")
        progress.emit(8, "context", "Building video context")
        build_context.build_context(video, transcript_path=args.transcript)
        progress.emit(16, "context", "Context ready")
    select_highlights.select_highlights(video,
                                        transcript_path=args.transcript,
                                        context_path=args.context,
                                        max_clips=args.max_clips,
                                        min_score=args.min_score,
                                        rules_summary=rules,
                                        progress=_scaled(16 if needed_context else 0,
                                                         100, "select"))
    _sync_reviewing(video)


def cmd_review(args):
    script = ROOT / "src" / "review_ui.py"
    subprocess.run([sys.executable, "-m", "streamlit", "run", str(script)])


def cmd_cut(args):
    if args.emit_progress:
        progress.enable()
    video = _resolve_video(args.video)
    candidates_path = Path(args.candidates) if args.candidates else _candidates_path(video)
    _, clips = _approved(candidates_path, auto=args.auto)
    if not clips:
        raise SystemExit("No approved clips to cut. Run review (or use --auto).")
    progress.emit(0, "cut", f"Cutting {len(clips)} clips")
    for r in cut_clips.cut_clips(video, clips, progress=_scaled(0, 100, "cut")):
        print(f"[cut] -> {r['path']} ({r['start']}-{r['end']}s)")
    progress.emit(100, "done", "Cutting finished")


def cmd_render(args):
    if args.emit_progress:
        progress.enable()
    video = _resolve_video(args.video)
    candidates_path = Path(args.candidates) if args.candidates else _candidates_path(video)
    data, clips = _approved(candidates_path, auto=args.auto)
    transcript_path = args.transcript or _transcript_path(video)
    template_name = args.template or config.default_template
    manifest = cut_clips.read_manifest(config.raw_dir, video.stem)
    manifest_clips = {Path(c["path"]).name: c for c in manifest["clips"]} if manifest else {}
    total = max(1, len(clips))
    for i, clip in enumerate(clips, start=1):
        raw = config.raw_dir / f"{video.stem}_clip_{i:02d}.mp4"
        if not raw.exists():
            raise FileNotFoundError(f"Raw clip missing (run cut first): {raw}")
        m = manifest_clips.get(raw.name)
        if m:
            start, end = m["start"], m["end"]
        else:
            start, end = cut_clips.padded_range(clip["start"], clip["end"], data.get("duration"))
        progress.emit(100 * (i - 1) / total, "render", f"Rendering clip {i}/{total}")
        out = apply_template.apply_template(raw, transcript_path, start, end,
                                            template_name=template_name,
                                            hook_text=clip.get("hook") or None)
        print(f"[render] -> {out}")
        progress.emit(100 * i / total, "render", f"Rendered clip {i}/{total}")
    progress.emit(100, "done", f"Rendered {total} clips")


def _prepare(video, args, t0, t1):
    """Run transcribe → clean → context → select; returns the candidates path.

    Returns None for email mode after the transcript has been emailed — the
    run finishes and the highlight reply arrives via the inbox poller."""
    camp = _active_campaign()
    if camp:
        camp_mod.add_analyzing_placeholder(camp, video.stem)
    progress.emit(t0, "transcribe", "Transcribing audio")
    transcribe.transcribe(video, progress=_scaled(t0, t0 + (t1 - t0) * 0.45, "transcribe"))
    if config.clean_transcript:
        progress.emit(t0 + (t1 - t0) * 0.45, "clean", "Fixing low-confidence words")
        clean_transcript.clean_transcript(video)
    progress.emit(t0 + (t1 - t0) * 0.5, "context", "Building video context")
    build_context.build_context(video)
    camp = _active_campaign()
    if _use_local_highlights(args, camp):
        progress.emit(t0 + (t1 - t0) * 0.55, "select", "Finding highlights with the LLM")
        camp = _active_campaign()
        rules = camp.rules_summary() if camp else None
        select_highlights.select_highlights(video, max_clips=args.max_clips,
                                            min_score=args.min_score,
                                            rules_summary=rules,
                                            progress=_scaled(t0 + (t1 - t0) * 0.55,
                                                             t0 + (t1 - t0) * 0.9, "select"))
        _sync_reviewing(video)
    else:
        progress.emit(t0 + (t1 - t0) * 0.55, "select", "Emailing the transcript")
        _email_await(video, camp)
        return None
    camp = _active_campaign()
    if camp:
        camp.touch()
    return _candidates_path(video)


def cmd_frames(args):
    """Extract frames for style analysis (side tool)."""
    if args.emit_progress:
        progress.enable()
    config.ensure_dirs()
    video = _resolve_video(args.video)
    manifest = extract_frames.extract_frames(
        video, mode=args.mode, n_frames=args.num, scene_threshold=args.threshold,
        width=args.width, grid=args.grid, max_frames=args.max_frames,
        progress=_scaled(0, 100, "frames"))
    n = len(manifest["frames"])
    sheets = manifest.get("sheets") or []
    print(f"[frames] {n} frame(s) extracted to {config.frames_dir / video.stem}")
    if sheets:
        print(f"[frames] contact sheets: {', '.join(sheets)}")
    progress.emit(100, "done", f"{n} frames extracted")


def cmd_style(args):
    """Analyze extracted frames -> style report + draft template."""
    if args.emit_progress:
        progress.enable()
    config.ensure_dirs()
    video = _resolve_video(args.video)
    report, template = analyze_frames.analyze_and_draft(
        video.stem, name=args.name, cta_text=args.cta_text,
        progress=_scaled(0, 90, "frames"))
    report_dir = config.frames_dir / video.stem
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / "style_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    camp = _active_campaign()
    if camp:
        tpl_path = camp.template_path
    else:
        tpl_path = config.root / "templates" / f"{args.name or video.stem + '_style'}.json"
    tpl_path.parent.mkdir(parents=True, exist_ok=True)
    tpl_path.write_text(json.dumps(template, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(f"[style] report  -> {report_path}")
    print(f"[style] template -> {tpl_path}")
    print(f"[style] layout={report['layout']} "
          f"hook={report['hook']['median_hex']} "
          f"captions={report['captions']['median_hex']} "
          f"keyword={report['captions']['keyword_hex']} "
          f"cta={report['cta']['median_hex']}")
    progress.emit(100, "done", "Style template drafted")


def _campaign_style_brief():
    camp = _active_campaign()
    if not camp:
        return ""
    return str(camp.settings().get("style_brief") or "").strip()


def _campaign_edit_instructions():
    camp = _active_campaign()
    if not camp:
        return ""
    return str(camp.settings().get("edit_instructions") or "").strip()


def _template_for(video, explicit=None):
    """Explicit flag wins; else a style-explorer winner for this stem; else
    the campaign/global default template."""
    if explicit:
        return explicit
    winner = style_explorer.winner_template_path(video.stem)
    if winner.exists():
        print(f"[export] using exploration winner template: {winner.name}")
        return str(winner)
    return config.default_template


def cmd_explore_style(args):
    """Explore edit styles on one probe clip, auto-pick the winning style
    with the local vision LLM, and persist it for full-quality rollout."""
    if args.emit_progress:
        progress.enable()
    config.ensure_dirs()
    video = _resolve_video(args.video)

    err = style_explorer.check_vision_ready()
    if err:
        raise SystemExit(f"[explore] {err}")

    candidates_path = _candidates_path(video)
    data, clips = _approved(candidates_path, auto=args.auto)
    if not clips:
        raise SystemExit("[explore] no approved clips. Approve clips in "
                         "review first, or use --auto.")
    if args.probe is not None:
        probe = next((c for c in clips if c.get("index") == args.probe), None)
        if probe is None and 0 < args.probe <= len(clips):
            probe = clips[args.probe - 1]
        if probe is None:
            raise SystemExit(f"[explore] probe clip #{args.probe} not found")
    else:
        probe = max(clips, key=lambda c: float(c.get("score") or 0.0))
    print(f"[explore] probe clip: {probe.get('start', 0):.2f}-"
          f"{probe.get('end', 0):.2f}s (score {probe.get('score')})")

    brief = (args.brief or "").strip() or _campaign_style_brief()
    transcript_path = _transcript_path(video)
    preview_dir = config.previews_dir / video.stem

    progress.emit(0, "explore-cut", "Cutting probe edge variants")
    edges = style_explorer.cut_probe_variants(
        video, probe, transcript_path, preview_dir)
    progress.emit(15, "explore-cut", f"{len(edges)} probe edge cut(s)")

    constraints = style_explorer.interpret_brief(brief)
    progress.emit(18, "explore-variants", "Generating style variants")
    variants = style_explorer.generate_variants(
        video.stem, max_variants=args.variants, constraints=constraints)
    print(f"[explore] {len(variants)} variant(s) generated")
    progress.emit(25, "explore-variants", f"{len(variants)} variants")

    probes = {
        "default": edges[0],
        "tight": edges[1] if len(edges) > 1 else edges[0],
        "extended_lead": edges[-1],
    }
    hook_text = probe.get("hook") or None
    n_variants = len(variants)
    preview_files = {}
    for i, (name, tpl, summary) in enumerate(variants):
        edge_key = ("extended_lead" if i == 0 else
                    "tight" if i == n_variants - 1 else "default")
        pr = probes[edge_key]
        progress.emit(25 + 55 * i / max(1, n_variants), "explore-render",
                      f"Rendering preview {i + 1}/{n_variants}: {name}")
        try:
            out = style_explorer.render_variant(
                i, tpl, {"path": pr["path"], "transcript": transcript_path,
                         "start": pr["start"], "end": pr["end"]},
                preview_dir, hook_text)
        except Exception as exc:  # noqa: BLE001
            print(f"[explore] render failed for {name}: {exc}")
            continue
        preview_files[i] = out

    progress.emit(80, "explore-judge", "Scoring previews with the vision LLM")
    scored_records = []
    for i, (name, tpl, summary) in enumerate(variants):
        pv = preview_files.get(i)
        if pv is None:
            continue
        frames = style_explorer.extract_frames(pv, preview_dir, prefix=f"v{i:02d}")
        verdict = style_explorer.judge_variant(frames, summary, brief)
        progress.emit(80 + 20 * (i + 1) / max(1, n_variants),
                      "explore-judge", f"Judged {i + 1}/{n_variants}")
        if verdict is None:
            print(f"[explore] {name}: unscorable, excluded")
            continue
        scored_records.append({
            "name": name,
            "file": pv.name,
            "edge": ("extended_lead" if i == 0 else
                     "tight" if i == n_variants - 1 else "default"),
            "summary": summary,
            "frames": [p.name for p in frames],
            "scores": verdict.get("scores", {}),
            "total": verdict.get("total"),
            "verdict": verdict.get("verdict", ""),
            "template": tpl,
        })

    winner = None
    if scored_records:
        winner = max(scored_records, key=lambda r: float(r["total"]))

    import datetime as _dt
    report = {
        "video_id": video.stem,
        "video": str(video),
        "brief": brief,
        "brief_constraints": constraints,
        "probe": {
            "start": probe.get("start"), "end": probe.get("end"),
            "score": probe.get("score"), "hook": hook_text,
        },
        "vision_model": config.vision_model,
        "variants": [{k: v for k, v in r.items() if k != "template"}
                     for r in scored_records],
        "winner": winner["name"] if winner else None,
        "timestamp": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
    }
    style_explorer.save_report(report)

    if winner is None:
        print("[explore] WARNING: no variant scored; falling back to the "
              f"default template '{config.default_template}'")
        progress.emit(100, "done", "Exploration finished (no winner scored)")
        return

    winner_path = style_explorer.winner_template_path(video.stem)
    winner_tpl = winner["template"]
    winner_tpl["name"] = f"{video.stem}_winner"
    winner_path.write_text(json.dumps(winner_tpl, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    style_explorer.save_report(dict(report, winner_template=winner_path.name))

    print(f"[explore] {len(scored_records)}/{n_variants} previews scored, "
          f"winner: {winner['name']} (total {float(winner['total']):.1f})")
    print(f"[explore] verdict: {winner['verdict']}")
    print(f"[explore] winner template -> {winner_path}")
    progress.emit(100, "done",
                  f"Winner: {winner['name']} ({float(winner['total']):.1f})")
    progress.event("explore_done", {
        "video_id": video.stem,
        "winner": winner["name"],
        "total": float(winner["total"]),
    })


def cmd_analyze(args):
    """transcribe â†’ context â†’ select. Produces candidates for review."""
    if args.emit_progress:
        progress.enable()
    config.ensure_dirs()
    video = _resolve_video(args.video)
    _prepare(video, args, 0, 100)
    progress.emit(100, "done", "Highlights ready for review")


def cmd_export(args):
    """cut + render approved clips (uses review decisions unless --auto)."""
    if args.emit_progress:
        progress.enable()
    config.ensure_dirs()
    video = _resolve_video(args.video)
    instructions = (getattr(args, "instructions", "") or "").strip()
    if not instructions:
        instructions = _campaign_edit_instructions()
    if instructions:
        print(f"[export] edit instructions: {instructions}")
    candidates_path = _candidates_path(video)
    data, clips = _approved(candidates_path, auto=args.auto)
    if not clips:
        raise SystemExit("No approved clips to export. Approve clips in review first.")

    progress.emit(0, "cut", f"Cutting {len(clips)} clips")
    results = cut_clips.cut_clips(video, clips, progress=_scaled(0, 45, "cut"))

    template_name = _template_for(video, args.template)
    transcript_path = _transcript_path(video)
    total = max(1, len(results))
    outputs = []
    for i, r in enumerate(results):
        progress.emit(45 + 55 * i / total, "render", f"Rendering clip {i + 1}/{total}")
        out = apply_template.apply_template(r["path"], transcript_path, r["start"], r["end"],
                                            template_name=template_name,
                                            hook_text=clips[i].get("hook") or None)
        outputs.append(out)
        print(f"[export] -> {out}")
    _sync_exported(video, clips)
    progress.emit(100, "done", f"Exported {len(results)} clips")
    progress.event("export_done", {
        "video_id": video.stem,
        "clip_count": len(outputs),
        "names": [Path(o).name for o in outputs],
    })


def cmd_pipeline(args):
    """Full auto run: analyze + cut + render (review skipped)."""
    if args.emit_progress:
        progress.enable()
    config.ensure_dirs()
    video = _resolve_video(args.video)
    candidates_path = _prepare(video, args, 0, 55)
    if candidates_path is None:
        print("[pipeline] email mode: waiting for the highlight reply; "
              "approve the clips in the Approval page when they arrive.")
        return

    _, clips = _approved(candidates_path, auto=True)
    if not clips:
        raise SystemExit("No clips found above threshold. Lower --min-score and retry.")

    progress.emit(55, "cut", f"Cutting {len(clips)} clips")
    results = cut_clips.cut_clips(video, clips, progress=_scaled(55, 75, "cut"))

    template_name = _template_for(video, args.template)
    transcript_path = _transcript_path(video)
    total = max(1, len(results))
    for i, r in enumerate(results):
        progress.emit(75 + 25 * i / total, "render", f"Rendering clip {i + 1}/{total}")
        out = apply_template.apply_template(r["path"], transcript_path, r["start"], r["end"],
                                            template_name=template_name,
                                            hook_text=clips[i].get("hook") or None)
        print(f"[pipeline] final -> {out}")

    _sync_exported(video, clips)
    progress.emit(100, "done", f"Done: {len(results)} clips exported")
    print(f"[pipeline] done: {len(results)} clips exported to {config.output_dir}")


def cmd_batch(args):
    config.ensure_dirs()
    videos = sorted([p for p in config.input_dir.iterdir()
                      if p.suffix.lower() in (".mp4", ".mov", ".mkv", ".webm", ".avi")])
    if not videos:
        raise SystemExit(f"No videos found in {config.input_dir}")
    for video in videos:
        print(f"\n=== processing {video.name} ===")
        candidates_path = _prepare(video, args, 0, 55)
        if candidates_path is None:
            print("[batch] email mode: waiting for the highlight reply; skipping clips")
            continue
        _, clips = _approved(candidates_path, auto=True)
        if not clips:
            print("[batch] no clips above threshold; skipping")
            continue
        results = cut_clips.cut_clips(video, clips)
        template_name = _template_for(video, args.template)
        transcript_path = _transcript_path(video)
        for i, r in enumerate(results):
            out = apply_template.apply_template(r["path"], transcript_path,
                                                r["start"], r["end"],
                                                template_name=template_name,
                                                hook_text=clips[i].get("hook") or None)
            print(f"[batch] final -> {out}")


def cmd_telegram_setup(args):
    """List recent chats that messaged the bot so the user can copy their
    chat id into .env (TELEGRAM_CHAT_ID). Read-only: never writes .env."""
    from src import notify
    token = config.telegram_bot_token
    if not token:
        print("TELEGRAM_BOT_TOKEN is not set in .env.")
        print("Create a bot with @BotFather on Telegram (/newbot), paste the "
              "token into .env, then re-run this command.")
        return
    try:
        resp = notify.get_updates_sync(token)
    except Exception as exc:  # noqa: BLE001
        print(f"Telegram API request failed: {exc}")
        return
    if not resp.get("ok"):
        print(f"Telegram API error: {resp.get('description') or resp}")
        return
    updates = resp.get("result") or []
    chats = {}
    for u in updates:
        msg = u.get("message") or {}
        chat = msg.get("chat")
        if not chat:
            continue
        cid = chat.get("id")
        if cid in chats:
            continue
        chats[cid] = chat.get("title") or " ".join(filter(None, [
            chat.get("first_name"), chat.get("last_name"),
            f"@{chat.get('username')}" if chat.get("username") else "",
        ])).strip() or "?"
    if not chats:
        print("No recent chats found (getUpdates was empty).")
        print("Open your bot in Telegram, send it any message (e.g. /start), "
              "then re-run: python main.py telegram-setup")
        return
    print("Recent chats that messaged the bot:")
    for cid, title in chats.items():
        print(f"  {title:<30} chat id: {cid}")
    print()
    print("Next steps:")
    print("  1. Copy your chat id into .env as TELEGRAM_CHAT_ID=<id>")
    print("     (group/channel ids are negative; the bot must be a member)")
    print("  2. Verify delivery:  python main.py telegram-test")


def cmd_telegram_test(args):
    """Send a test message with the current config; print the API result."""
    from src import notify
    token = config.telegram_bot_token
    chat = config.telegram_chat_id
    if not config.telegram_enabled:
        print("Telegram is disabled (telegram.enabled=false in config.json).")
        return
    if not token or not chat:
        missing = []
        if not token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not chat:
            missing.append("TELEGRAM_CHAT_ID")
        print(f"Not configured — missing in .env: {', '.join(missing)}")
        print("Run `python main.py telegram-setup` to find your chat id.")
        return
    try:
        resp = notify.send_message_sync(
            token, chat,
            "🔔 <b>ClipForge test</b>\n• Telegram delivery works\n"
            f"<i>token {_mask(token)} · chat {chat}</i>")
    except Exception as exc:  # noqa: BLE001
        print(f"Telegram API request failed: {exc}")
        return
    if resp.get("ok"):
        print("ok=true — test message delivered. Check your Telegram chat.")
    else:
        print(f"ok=false — {resp.get('description') or resp}")


def _mask(token):
    return f"{token[:6]}…" if token else ""


def cmd_email_check(args):
    if args.emit_progress:
        progress.enable()
    summaries = email_highlights.poll_highlight_emails(
        on_ingested=lambda s: progress.event("highlights_received", s))
    print(f"[email] inbox check complete: {len(summaries)} highlight message(s) ingested")
    for s in summaries:
        print(f"[email]   {s.get('video_id')}: {s.get('clip_count')} clip(s)")
    if not summaries:
        print("[email] no new highlight replies (they must come from "
              f"{config.highlight_reply_sender or 'the configured sender'})")


def main():
    parser = argparse.ArgumentParser(prog="clipforge", description="AI auto-clipper pipeline.")
    parser.add_argument("--emit-progress", action="store_true",
                        help="Emit @@PROGRESS@@ JSON lines on stdout for the web UI.")
    parser.add_argument("--campaign", help="Scope pipeline dirs to this campaign id.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("transcribe", help="Transcribe a video (Phase 1)")
    p.add_argument("video")
    p.add_argument("--model"); p.add_argument("--device")
    p.add_argument("--compute"); p.add_argument("--language")
    p.add_argument("--skip-email", action="store_true",
                   help="local mode: skip emailing the transcript out")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("clean", help="Fix low-confidence transcript words via LLM (Phase 1.7)")
    p.add_argument("video")
    p.add_argument("--transcript")
    p.set_defaults(func=cmd_clean)

    p = sub.add_parser("context", help="Build per-video context (Phase 1.5)")
    p.add_argument("video"); p.add_argument("--transcript")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("select", help="LLM highlight selection (Phase 2)")
    p.add_argument("video"); p.add_argument("--transcript"); p.add_argument("--context")
    p.add_argument("--max-clips", type=int); p.add_argument("--min-score", type=float)
    p.add_argument("--email", action="store_true",
                   help="email mode: send transcript, wait for the AI highlight reply")
    p.add_argument("--local", action="store_true",
                   help="force local Ollama select even when the campaign uses email highlights")
    p.set_defaults(func=cmd_select)

    p = sub.add_parser("review", help="Launch Streamlit review UI (legacy)")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("cut", help="Cut approved clips (Phase 3)")
    p.add_argument("video"); p.add_argument("--candidates"); p.add_argument("--auto", action="store_true")
    p.set_defaults(func=cmd_cut)

    p = sub.add_parser("render", help="Apply templates to raw clips (Phase 5)")
    p.add_argument("video"); p.add_argument("--candidates"); p.add_argument("--transcript")
    p.add_argument("--template"); p.add_argument("--auto", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("frames", help="Extract frames for style analysis")
    p.add_argument("video")
    p.add_argument("--mode", choices=["uniform", "scene"], default="uniform")
    p.add_argument("--num", type=int, default=12, help="uniform: number of frames")
    p.add_argument("--threshold", type=float, default=0.3, help="scene: change threshold")
    p.add_argument("--max-frames", dest="max_frames", type=int, default=24, help="scene: cap")
    p.add_argument("--width", type=int, default=360, help="frame width in px")
    p.add_argument("--grid", help='optional contact sheet, e.g. "3x4"')
    p.set_defaults(func=cmd_frames)

    p = sub.add_parser("style", help="Analyze frames -> draft style template")
    p.add_argument("video")
    p.add_argument("--name", help="template name (default <stem>_style)")
    p.add_argument("--cta-text", dest="cta_text", default="Follow for more!")
    p.set_defaults(func=cmd_style)

    p = sub.add_parser("explore-style",
                       help="Explore edit styles on a probe clip (vision-LLM auto-select)")
    p.add_argument("video")
    p.add_argument("--brief", help="style brief text (overrides campaign brief)")
    p.add_argument("--variants", type=int, help="number of variants (default from config)")
    p.add_argument("--probe", type=int, help="probe clip index (default: highest score)")
    p.add_argument("--auto", action="store_true",
                   help="use score-threshold candidates, not just approved")
    p.set_defaults(func=cmd_explore_style)

    p = sub.add_parser("analyze", help="transcribe -> context -> select (for review)")
    p.add_argument("video"); p.add_argument("--max-clips", type=int); p.add_argument("--min-score", type=float)
    p.add_argument("--no-local-highlights", action="store_true",
                   help="skip local Ollama select; wait for AI highlights by email")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("export", help="cut + render approved clips")
    p.add_argument("video"); p.add_argument("--template"); p.add_argument("--auto", action="store_true")
    p.add_argument("--instructions", help="edit instructions to log with this export")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("pipeline", help="Full auto pipeline (Phase 6)")
    p.add_argument("video"); p.add_argument("--skip-review", action="store_true")
    p.add_argument("--auto", action="store_true")
    p.add_argument("--template"); p.add_argument("--max-clips", type=int)
    p.add_argument("--min-score", type=float)
    p.add_argument("--no-local-highlights", action="store_true",
                   help="skip local Ollama select; wait for AI highlights by email")
    p.set_defaults(func=cmd_pipeline)

    p = sub.add_parser("check-email", help="Poll the inbox for AI highlight replies")
    p.set_defaults(func=cmd_email_check)

    p = sub.add_parser("telegram-setup",
                       help="List recent Telegram chats so you can copy your chat id into .env")
    p.set_defaults(func=cmd_telegram_setup)

    p = sub.add_parser("telegram-test",
                       help="Send a Telegram test message using the current config")
    p.set_defaults(func=cmd_telegram_test)

    p = sub.add_parser("batch", help="Process a whole folder (Phase 6)")
    p.add_argument("--template"); p.add_argument("--max-clips", type=int)
    p.add_argument("--min-score", type=float)
    p.add_argument("--no-local-highlights", action="store_true",
                   help="skip local Ollama select; wait for AI highlights by email")
    p.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    if args.campaign:
        config.activate_campaign(args.campaign)
    args.func(args)


if __name__ == "__main__":
    main()

