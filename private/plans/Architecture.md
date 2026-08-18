# Architecture.md — ClipForge

## App Flow
1. **Input** — user drops a video file into `/input` (or uploads via the web UI)
2. **Transcription Stage** — `faster-whisper` (local, GPU-accelerated) generates a word-level timestamped transcript, saved as JSON
3. **Context Stage** — heuristic classifier writes a per-video `context.json` (type/tone/topics/platform) used to ground the LLM prompt
4. **Highlight Selection Stage** — transcript JSON sent to an LLM (local via Ollama, or API) with a scoring prompt → returns a list of candidate clips: `{start, end, reason, score}`
5. **Review Stage** — web UI shows candidate clips with transcript snippet + reason/score, lets user approve/reject/adjust timestamps
6. **Cutting Stage** — ffmpeg cuts each approved clip from the source video (sources taller than `cutting.max_raw_height` are downscaled at this step)
7. **Auto-Edit Stage** — template engine applies: aspect ratio crop, burned-in captions (from word timestamps), optional overlays — via ffmpeg filters
8. **Export** — final clips written to `/output`, named and numbered

## Interfaces
- **Web UI (primary):** `server.py` (Starlette + uvicorn) serves `web/` and runs the
  pipeline as a subprocess (`main.py --emit-progress`), streaming progress + logs.
  Start with `start_clipforge.bat`.
- **CLI:** `main.py` accepts video paths, filenames, or bare stems (`python main.py analyze sample3`).
- **Streamlit review UI:** `src/review_ui.py` remains as a legacy alternative.

## Folder Structure
```
clipforge/
├── input/                  # raw source videos
├── output/                 # final rendered clips
│   └── raw/                # un-edited cut clips + <video>_manifest.json
├── data/
│   ├── transcripts/        # whisper JSON output per video
│   ├── context/            # per-video context files
│   └── clip_candidates/    # LLM-selected clip lists (with approve/reject status)
├── templates/              # editing templates (JSON configs)
│   └── square_captioned.json
├── web/                    # web UI frontend (index.html / app.js / style.css)
├── src/
│   ├── config.py           # loads config.json + .env
│   ├── transcribe.py       # whisper wrapper
│   ├── build_context.py    # per-video context builder
│   ├── select_highlights.py# LLM prompt + call + parse (chunked, retry, dedupe)
│   ├── cut_clips.py        # ffmpeg cut logic + cut manifest writer
│   ├── apply_template.py   # auto-edit rendering (crop/scale + ASS captions)
│   ├── progress.py         # @@PROGRESS@@ emitter for the web UI
│   └── review_ui.py        # legacy Streamlit review app
├── config.json             # user settings
├── .env.example            # secrets/overrides
├── main.py                 # orchestrates the pipeline (CLI)
├── server.py               # web server (Starlette REST API + static UI)
└── PRD.md / Architecture.md / Rules.md / Phases.md / Design.md / Memory.md
```

## Data Flow Between Stages
- Every stage reads/writes JSON under `data/`, so any stage can be re-run alone.
- `cut_clips` writes `output/raw/<video>_manifest.json` with the **actual padded**
  start/end of each raw clip; the render stage prefers the manifest over recomputed
  ranges, keeping caption timing exact even if candidates are edited after cutting.

## Tech Stack
- **Transcription:** faster-whisper (local, GPU)
- **LLM (highlight selection):** Ollama with a local model (e.g. gemma/llama)
- **Video cutting/rendering:** ffmpeg (via subprocess)
- **Web UI:** Starlette + uvicorn backend, vanilla JS frontend (no build step)
- **Language:** Python throughout, single environment
- **Storage:** local filesystem only, JSON files
