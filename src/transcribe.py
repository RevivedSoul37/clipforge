"""Phase 1 — Transcription pipeline.

Wraps faster-whisper to produce a word-level, timestamped transcript JSON for a
single video. Extracts a 16k mono audio track with ffmpeg first, transcribes it,
and writes the result to data/transcripts/<video_id>_transcript.json.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from src.config import config


def _register_cuda_dll_dirs():
    """Make pip-installed NVIDIA CUDA runtime DLLs (cuBLAS/cuDNN/cudart)
    discoverable by CTranslate2 on Windows."""
    if sys.platform != "win32":
        return
    for site in list(sys.path):
        nvidia = Path(site) / "nvidia"
        if not nvidia.is_dir():
            continue
        for bin_dir in nvidia.glob("*/bin"):
            if bin_dir.is_dir():
                try:
                    os.add_dll_directory(str(bin_dir))
                except OSError:
                    pass


def _probe_duration(video_path: Path) -> float:
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration",
           "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError(f"ffprobe failed on {video_path}:\n{proc.stderr[-2000:]}")
    return float(proc.stdout.strip())


def _extract_audio(video_path: Path, audio_path: Path) -> Path:
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "pcm_s16le", str(audio_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed to extract audio from {video_path}:\n{proc.stderr[-2000:]}")
    return audio_path


def transcribe(video_path, model_size=None, device=None, compute_type=None,
               language=None, output_dir=None, progress=None):
    video_path = Path(video_path)
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    _register_cuda_dll_dirs()
    from faster_whisper import WhisperModel

    model_size = model_size or config.whisper_model
    device = device or config.whisper_device
    compute_type = compute_type or config.whisper_compute
    language = language if language is not None else config.whisper_language

    print(f"[transcribe] loading faster-whisper model '{model_size}' "
          f"(device={device}, compute={compute_type})")
    model = WhisperModel(model_size, device=device, compute_type=compute_type)

    try:
        duration = _probe_duration(video_path)
    except Exception:  # noqa: BLE001
        duration = None

    with tempfile.TemporaryDirectory() as tmp:
        audio_path = Path(tmp) / "audio.wav"
        print("[transcribe] extracting audio track")
        _extract_audio(video_path, audio_path)

        print("[transcribe] transcribing (word timestamps enabled)")
        segments, info = model.transcribe(
            str(audio_path),
            word_timestamps=True,
            language=language,
            vad_filter=True,
        )

        result = {
            "video_id": video_path.stem,
            "source": str(video_path),
            "duration": round(float(info.duration), 3),
            "language": info.language,
            "language_probability": round(float(info.language_probability), 3),
            "model": model_size,
            "segments": [],
        }
        for seg in segments:
            words = [
                {
                    "word": w.word,
                    "start": round(float(w.start), 3),
                    "end": round(float(w.end), 3),
                    "probability": round(float(w.probability), 3) if w.probability is not None else None,
                }
                for w in (seg.words or [])
            ]
            result["segments"].append({
                "start": round(float(seg.start), 3),
                "end": round(float(seg.end), 3),
                "text": seg.text.strip(),
                "words": words,
            })
            if progress and duration:
                progress(min(1.0, float(seg.end) / duration))

    out_dir = Path(output_dir) if output_dir else config.transcripts_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{video_path.stem}_transcript.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[transcribe] saved transcript -> {out_path}")
    return out_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Transcribe a video with faster-whisper.")
    parser.add_argument("video")
    parser.add_argument("--model")
    parser.add_argument("--device")
    parser.add_argument("--compute")
    parser.add_argument("--language")
    args = parser.parse_args()
    transcribe(args.video, model_size=args.model, device=args.device,
               compute_type=args.compute, language=args.language)
