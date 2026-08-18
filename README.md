# ClipForge

Local AI auto-clipper: long video in, post-ready vertical clips out.

Pipeline: faster-whisper transcription → Ollama highlight selection (with user
rules + emotion b-roll cues) → ffmpeg cutting → template rendering (9:16 square
band, hook title + gradient captions, bundled fonts) → background music mix →
b-roll cutaways from a per-emotion library.

## Requirements

- Windows, Python 3.12, ffmpeg on PATH, Ollama with a local model
- `pip install -r requirements.txt` into `.venv`
- GPU optional but recommended for faster-whisper

## Run

Double-click `start_clipforge.bat`, open http://localhost:8600.

Pages: **Source** (upload/pick video) → **Analyze** (transcribe + LLM picks) →
**Review** (approve/reject/edit hooks & times, preview) → **Export** (cut +
render with music and b-roll).

## Configuration

- `config.json` — models, thresholds, default template, paths
- `.env` (see `.env.example`) — whisper/ollama overrides, `PEXELS_API_KEY` /
  `PIXABAY_API_KEY` for stock b-roll auto-fill
- `templates/square_captioned.json` — the house style (hook, captions, music,
  b-roll mode)
- `data/selection_rules.txt` — extra AI selection rules (also editable in UI)
- `data/broll/<emotion>/` — b-roll library (stock or AI-generated clips/images)
- `music/` — background tracks (nasheeds/BGM)

## Layout

- `main.py` — CLI orchestrator
- `server.py` — web backend (Starlette)
- `src/` — pipeline stages
- `web/` — frontend
- `private/` — planning docs and session memory (non-production)
