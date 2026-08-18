"""ClipForge — pipeline orchestrator.

Stages: input → transcribe → context → select highlights → review → cut → auto-edit → output.

Commands:
  analyze     transcribe → context → select (produces candidates for review)
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
from src import fetch_broll
from src import progress

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


def _transcript_path(video):
    return config.transcripts_dir / f"{video.stem}_transcript.json"


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


def cmd_transcribe(args):
    if args.emit_progress:
        progress.enable()
    progress.emit(0, "transcribe", "Loading model and extracting audio")
    transcribe.transcribe(_resolve_video(args.video), model_size=args.model,
                          device=args.device, compute_type=args.compute,
                          language=args.language,
                          progress=_scaled(0, 100, "transcribe"))
    progress.emit(100, "done", "Transcript saved")


def cmd_context(args):
    if args.emit_progress:
        progress.enable()
    progress.emit(0, "context", "Building video context")
    build_context.build_context(_resolve_video(args.video), transcript_path=args.transcript)
    progress.emit(100, "done", "Context saved")


def cmd_select(args):
    if args.emit_progress:
        progress.enable()
    select_highlights.select_highlights(_resolve_video(args.video),
                                        transcript_path=args.transcript,
                                        context_path=args.context,
                                        max_clips=args.max_clips,
                                        min_score=args.min_score,
                                        progress=_scaled(0, 100, "select"))


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


def _broll_for(manifest, index):
    if not manifest:
        return None
    for entry in manifest.get("clips", []):
        if entry.get("clip_index") == index:
            return entry.get("cues")
    return None


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
    broll_manifest = fetch_broll.read_manifest(video.stem)
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
                                            hook_text=clip.get("hook") or None,
                                            broll_cues=_broll_for(broll_manifest, i))
        print(f"[render] -> {out}")
        progress.emit(100 * i / total, "render", f"Rendered clip {i}/{total}")
    progress.emit(100, "done", f"Rendered {total} clips")


def _prepare(video, args, t0, t1):
    """Run transcribe → context → select → broll; returns candidates path."""
    progress.emit(t0, "transcribe", "Transcribing audio")
    transcribe.transcribe(video, progress=_scaled(t0, t0 + (t1 - t0) * 0.5, "transcribe"))
    progress.emit(t0 + (t1 - t0) * 0.5, "context", "Building video context")
    build_context.build_context(video)
    progress.emit(t0 + (t1 - t0) * 0.55, "select", "Finding highlights with the LLM")
    select_highlights.select_highlights(video, max_clips=args.max_clips,
                                        min_score=args.min_score,
                                        progress=_scaled(t0 + (t1 - t0) * 0.55,
                                                         t0 + (t1 - t0) * 0.9, "select"))
    progress.emit(t0 + (t1 - t0) * 0.92, "broll", "Resolving b-roll library")
    data, clips = cut_clips.load_candidates(_candidates_path(video))
    fetch_broll.build_manifest(video.stem, clips)
    return _candidates_path(video)


def cmd_broll(args):
    if args.emit_progress:
        progress.enable()
    config.ensure_dirs()
    if args.fetch:
        progress.emit(0, "broll", "Fetching stock b-roll")
        report = fetch_broll.fetch_missing(progress=_scaled(0, 50, "broll"))
        print(f"[broll] fetched: {report}")
    video = _resolve_video(args.video)
    candidates_path = Path(args.candidates) if args.candidates else _candidates_path(video)
    if not candidates_path.exists():
        raise FileNotFoundError(f"Candidates not found: {candidates_path}. Run analyze first.")
    _, clips = cut_clips.load_candidates(candidates_path)
    progress.emit(60, "broll", "Resolving b-roll cues")
    fetch_broll.build_manifest(video.stem, clips)
    progress.emit(100, "done", "B-roll manifest ready")


def cmd_analyze(args):
    """transcribe → context → select. Produces candidates for review."""
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
    candidates_path = _candidates_path(video)
    data, clips = _approved(candidates_path, auto=args.auto)
    if not clips:
        raise SystemExit("No approved clips to export. Approve clips in review first.")

    progress.emit(0, "cut", f"Cutting {len(clips)} clips")
    results = cut_clips.cut_clips(video, clips, progress=_scaled(0, 45, "cut"))

    template_name = args.template or config.default_template
    transcript_path = _transcript_path(video)
    duration = data.get("duration")
    broll_manifest = fetch_broll.read_manifest(video.stem)
    total = max(1, len(results))
    for i, r in enumerate(results):
        progress.emit(45 + 55 * i / total, "render", f"Rendering clip {i + 1}/{total}")
        out = apply_template.apply_template(r["path"], transcript_path, r["start"], r["end"],
                                            template_name=template_name,
                                            hook_text=clips[i].get("hook") or None,
                                            broll_cues=_broll_for(broll_manifest, i + 1))
        print(f"[export] -> {out}")
    progress.emit(100, "done", f"Exported {len(results)} clips")


def cmd_pipeline(args):
    """Full auto run: analyze + cut + render (review skipped)."""
    if args.emit_progress:
        progress.enable()
    config.ensure_dirs()
    video = _resolve_video(args.video)
    candidates_path = _prepare(video, args, 0, 55)

    _, clips = _approved(candidates_path, auto=True)
    if not clips:
        raise SystemExit("No clips found above threshold. Lower --min-score and retry.")

    progress.emit(55, "cut", f"Cutting {len(clips)} clips")
    results = cut_clips.cut_clips(video, clips, progress=_scaled(55, 75, "cut"))

    template_name = args.template or config.default_template
    transcript_path = _transcript_path(video)
    broll_manifest = fetch_broll.read_manifest(video.stem)
    total = max(1, len(results))
    for i, r in enumerate(results):
        progress.emit(75 + 25 * i / total, "render", f"Rendering clip {i + 1}/{total}")
        out = apply_template.apply_template(r["path"], transcript_path, r["start"], r["end"],
                                            template_name=template_name,
                                            hook_text=clips[i].get("hook") or None,
                                            broll_cues=_broll_for(broll_manifest, i + 1))
        print(f"[pipeline] final -> {out}")

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
        _, clips = _approved(candidates_path, auto=True)
        if not clips:
            print("[batch] no clips above threshold; skipping")
            continue
        results = cut_clips.cut_clips(video, clips)
        template_name = args.template or config.default_template
        transcript_path = _transcript_path(video)
        broll_manifest = fetch_broll.read_manifest(video.stem)
        for i, r in enumerate(results):
            out = apply_template.apply_template(r["path"], transcript_path,
                                                r["start"], r["end"],
                                                template_name=template_name,
                                                hook_text=clips[i].get("hook") or None,
                                                broll_cues=_broll_for(broll_manifest, i + 1))
            print(f"[batch] final -> {out}")


def main():
    parser = argparse.ArgumentParser(prog="clipforge", description="AI auto-clipper pipeline.")
    parser.add_argument("--emit-progress", action="store_true",
                        help="Emit @@PROGRESS@@ JSON lines on stdout for the web UI.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("transcribe", help="Transcribe a video (Phase 1)")
    p.add_argument("video")
    p.add_argument("--model"); p.add_argument("--device")
    p.add_argument("--compute"); p.add_argument("--language")
    p.set_defaults(func=cmd_transcribe)

    p = sub.add_parser("context", help="Build per-video context (Phase 1.5)")
    p.add_argument("video"); p.add_argument("--transcript")
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("select", help="LLM highlight selection (Phase 2)")
    p.add_argument("video"); p.add_argument("--transcript"); p.add_argument("--context")
    p.add_argument("--max-clips", type=int); p.add_argument("--min-score", type=float)
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

    p = sub.add_parser("analyze", help="transcribe -> context -> select (for review)")
    p.add_argument("video"); p.add_argument("--max-clips", type=int); p.add_argument("--min-score", type=float)
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("broll", help="resolve/fetch b-roll for a video's candidates")
    p.add_argument("video"); p.add_argument("--candidates")
    p.add_argument("--fetch", action="store_true", help="download missing stock first")
    p.set_defaults(func=cmd_broll)

    p = sub.add_parser("export", help="cut + render approved clips")
    p.add_argument("video"); p.add_argument("--template"); p.add_argument("--auto", action="store_true")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("pipeline", help="Full auto pipeline (Phase 6)")
    p.add_argument("video"); p.add_argument("--skip-review", action="store_true")
    p.add_argument("--auto", action="store_true")
    p.add_argument("--template"); p.add_argument("--max-clips", type=int)
    p.add_argument("--min-score", type=float)
    p.set_defaults(func=cmd_pipeline)

    p = sub.add_parser("batch", help="Process a whole folder (Phase 6)")
    p.add_argument("--template"); p.add_argument("--max-clips", type=int)
    p.add_argument("--min-score", type=float)
    p.set_defaults(func=cmd_batch)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
