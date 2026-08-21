"""Phase 3 — Cutting engine.

ffmpeg wrapper that cuts approved segments from the source video into raw clips.
Uses fast input seeking plus re-encode, which keeps cuts frame-accurate while
still skipping ahead quickly. Raw (un-edited) clips land in output/raw/.

Each cut records its *actual* (padded) start/end so the caption stage can shift
word timestamps to the raw clip's local timeline exactly.

Raw clips are downscaled to `cutting.max_raw_height` (default 1080) when the
source is larger, since the templates render to 1080p at most — without this a
4K source produces multi-hundred-MB intermediates that get scaled down anyway.
"""
import json
import subprocess
from pathlib import Path

from src.config import config


def _probe_duration(path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"ffprobe failed on {path}:\n{proc.stderr[-2000:]}")
    return float(proc.stdout.strip())


def _probe_size(path: Path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        w, h = proc.stdout.strip().split(",")[:2]
        return int(w), int(h)
    except (ValueError, IndexError):
        return None


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd)}\n{proc.stderr[-2000:]}")


def _downscale_args(w, h, max_height):
    """Scale-down filter args if the source exceeds max_height, else []."""
    if not max_height or h is None or h <= max_height:
        return []
    return ["-vf", f"scale=-2:{max_height}", "-sws_flags", "bicubic"]


def _ffmpeg_cut(video_path, start, end, out_path, src_size=None):
    """Exact cut of [start, end) — no further padding applied."""
    duration = max(0.1, end - start)
    w, h = src_size or (None, None)
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{start:.3f}",
        "-i", str(video_path),
        "-t", f"{duration:.3f}",
        "-map", "0:v:0", "-map", "0:a:0?",
        *_downscale_args(w, h, config.max_raw_height),
        "-c:v", config.video_codec, "-preset", config.preset, "-crf", str(config.crf),
        "-c:a", config.audio_codec, "-b:a", "192k",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def _pad(start, end, duration, lead_in=None, lead_out=None):
    if lead_in is None:
        lead_in = min(config.lead_in, 0.08) if config.vad_enabled else config.lead_in
    if lead_out is None:
        lead_out = min(config.lead_out, 0.1) if config.vad_enabled else config.lead_out
    start = max(0.0, float(start) - lead_in)
    end = min(float(duration), float(end) + lead_out)
    return start, end


def padded_range(start, end, duration, lead_in=None, lead_out=None):
    """Public helper: the actual (padded) cut range for a candidate clip."""
    return _pad(start, end, duration, lead_in, lead_out)


def cut_one(video_path, start, end, out_path, lead_in=None, lead_out=None):
    """Cut a single segment, applying padding. Returns (path, actual_start, actual_end)."""
    video_path = Path(video_path)
    duration = _probe_duration(video_path)
    start, end = _pad(start, end, duration, lead_in, lead_out)
    if start >= end:
        raise ValueError(f"Invalid padded range: {start:.2f}-{end:.2f}")
    _ffmpeg_cut(video_path, start, end, out_path, src_size=_probe_size(video_path))
    return out_path, round(start, 3), round(end, 3)


def cut_clips(video_path, clips, output_dir=None, progress=None):
    """Cut a list of clips [{start, end, ...}], returning metadata incl. actual times."""
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    duration = _probe_duration(video_path)
    src_size = _probe_size(video_path)
    out_dir = Path(output_dir) if output_dir else config.raw_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    total = max(1, len(clips))
    results = []
    for i, clip in enumerate(clips, start=1):
        start, end = float(clip["start"]), float(clip["end"])
        if start < 0 or end > duration + 1 or start >= end:
            raise ValueError(
                f"Clip {i} timestamps out of range: {start:.2f}-{end:.2f} "
                f"(video duration {duration:.2f})")
        start, end = _pad(start, end, duration)
        out_path = out_dir / f"{video_path.stem}_clip_{i:02d}.mp4"
        print(f"[cut] clip {i}: {start:.2f}-{end:.2f}s -> {out_path.name}")
        _ffmpeg_cut(video_path, start, end, out_path, src_size=src_size)
        results.append({
            "index": i,
            "start": round(start, 3),
            "end": round(end, 3),
            "path": str(out_path),
        })
        if progress:
            progress(i / total)

    write_manifest(out_dir, video_path, results)
    return results


def manifest_path(out_dir, video_stem):
    return Path(out_dir) / f"{video_stem}_manifest.json"


def write_manifest(out_dir, video_path, results):
    """Persist the actual (padded) cut ranges so later stages (render) read
    ground truth instead of re-deriving ranges that may have changed."""
    data = {
        "video_id": Path(video_path).stem,
        "source": str(video_path),
        "clips": results,
    }
    mp = manifest_path(out_dir, Path(video_path).stem)
    mp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return mp


def read_manifest(out_dir, video_stem):
    mp = manifest_path(out_dir, video_stem)
    if not mp.exists():
        return None
    try:
        return json.loads(mp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_candidates(candidates_path, only_status=None):
    data = json.loads(Path(candidates_path).read_text(encoding="utf-8"))
    clips = data.get("clips", [])
    if only_status:
        clips = [c for c in clips if c.get("status") in only_status]
    return data, clips


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Cut approved clips from a video.")
    parser.add_argument("video")
    parser.add_argument("--candidates")
    args = parser.parse_args()
    _, clips = load_candidates(
        args.candidates or config.candidates_dir / f"{Path(args.video).stem}_candidates.json",
        only_status={"approved"},
    )
    if not clips:
        raise SystemExit("No approved clips to cut.")
    cut_clips(args.video, clips)
