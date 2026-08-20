"""Frame extraction for style analysis.

Extracts still frames from a video (uniform sampling or scene-change
detection) into data/frames/<video_stem>/, optionally tiling them into
contact sheets for quick review. Writes a manifest.json mapping every
frame file to its source timestamp.

Used by the `frames` CLI command and the style-extraction loop:
    frames -> analyze_frames -> draft template JSON.
"""
import json
import subprocess
from pathlib import Path

from src.config import config

IMAGE_EXTS = (".jpg", ".jpeg", ".png", ".webp")


def _probe_duration(path: Path) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}:\n{proc.stderr[-1000:]}")
    try:
        return float(proc.stdout.strip())
    except ValueError:
        raise RuntimeError(f"Could not read duration of {path}") from None


def _probe_size(path: Path):
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}:\n{proc.stderr[-1000:]}")
    w, h = proc.stdout.strip().split(",")
    return int(w), int(h)


def _run_ffmpeg(args, what):
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed ({what}):\n{proc.stderr[-1500:]}")


def _scene_times(video, threshold):
    """Timestamps of scene-cut frames via ffprobe (best-effort)."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=pts_time", "-of", "csv=p=0",
         "-vf", f"select='gt(scene,{float(threshold)})'",
         str(video)],
        capture_output=True, text=True)
    times = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            times.append(round(float(line), 3))
        except ValueError:
            continue
    return times


def _parse_grid(grid):
    try:
        cols, rows = grid.lower().split("x")
        return max(1, int(cols)), max(1, int(rows))
    except (ValueError, AttributeError):
        raise ValueError(f"Bad grid spec '{grid}' (expected CxR, e.g. 3x4)") from None


def extract_frames(video, mode="uniform", n_frames=12, scene_threshold=0.3,
                   width=360, grid=None, max_frames=24, progress=None):
    """Extract frames from `video`; returns the manifest dict.

    mode:   "uniform" (n_frames evenly spaced) or "scene" (scene-change
            detection at scene_threshold, capped at max_frames).
    width:  downscale width in px (aspect preserved).
    grid:   optional "CxR" contact sheets tiled from the numbered frames.
    progress: optional callback(frac).
    """
    video = Path(video)
    if not video.exists():
        raise FileNotFoundError(f"Video not found: {video}")
    if mode not in ("uniform", "scene"):
        raise ValueError(f"Unknown frames mode: {mode}")

    duration = _probe_duration(video)
    vw, vh = _probe_size(video)

    out_dir = config.frames_dir / video.stem
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.iterdir():
        if old.is_file() and old.suffix.lower() in IMAGE_EXTS:
            old.unlink()

    frames = []
    if mode == "uniform":
        n = max(2, min(int(n_frames), 60))
        fps = (n - 1) / duration if duration > 0 else 1.0
        _run_ffmpeg(
            ["ffmpeg", "-y", "-v", "error", "-i", str(video),
             "-vf", f"fps={fps:.6f},scale={width}:-2",
             "-frames:v", str(n), "-q:v", "3",
             str(out_dir / "f_%04d.jpg")], "uniform frame extraction")
        step = duration / n if n else duration
        for i in range(1, n + 1):
            frames.append({"file": f"f_{i:04d}.jpg", "time": round(i * step, 3)})
        if progress:
            progress(0.8)
    else:
        n = max(2, min(int(max_frames), 60))
        _run_ffmpeg(
            ["ffmpeg", "-y", "-v", "error", "-i", str(video),
             "-vf", f"select='gt(scene,{float(scene_threshold)})',scale={width}:-2",
             "-vsync", "vfr", "-frames:v", str(n), "-q:v", "3",
             str(out_dir / "f_%04d.jpg")], "scene frame extraction")
        kept = sorted(p.name for p in out_dir.glob("f_*.jpg"))
        times = _scene_times(video, scene_threshold)
        for i, name in enumerate(kept):
            frames.append({"file": name,
                           "time": times[i] if i < len(times) else None})
        if progress:
            progress(0.8)

    manifest = {
        "video": str(video),
        "stem": video.stem,
        "duration": round(duration, 3),
        "source_resolution": [vw, vh],
        "mode": mode,
        "width": int(width),
        "frames": frames,
    }

    # optional contact sheets: numbered sequence -> tile emits sheet_1, sheet_2...
    if grid and frames:
        cols, rows = _parse_grid(grid)
        _run_ffmpeg(
            ["ffmpeg", "-y", "-v", "error",
             "-i", str(out_dir / "f_%04d.jpg"),
             "-vf", f"tile={cols}x{rows}",
             str(out_dir / "sheet_%02d.jpg")], "contact sheet")
        sheets = sorted(p.name for p in out_dir.glob("sheet_*.jpg"))
        manifest["sheets"] = sheets
        if progress:
            progress(0.95)

    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    if progress:
        progress(1.0)
    return manifest


def read_manifest(stem):
    p = config.frames_dir / stem / "manifest.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))
