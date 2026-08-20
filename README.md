# ClipForge

Local AI auto-clipper: long video in, post-ready vertical clips out.

Pipeline: faster-whisper transcription (language pinned + vocabulary-biasing
prompt) → LLM transcript cleanup (fixes mishearings like "jym"→"gym" using
Whisper's per-word confidence) → Ollama highlight selection (clips chosen by
segment id + user rules + emotion b-roll cues) → ffmpeg cutting → template
rendering (9:16 square band, hook title + gradient captions, bundled fonts)
→ background music mix → b-roll cutaways from a per-emotion library.

## Requirements

- Windows, Python 3.12, ffmpeg on PATH, Ollama with a local model
- `pip install -r requirements.txt` into `.venv`
- GPU optional but recommended for faster-whisper

## Run

Double-click `start_clipforge.bat`, open http://localhost:8600.

Pages: **Source** (upload/pick video) → **Analyze** (transcribe + LLM picks) →
**Review** (approve/reject/edit hooks & times, preview) → **Export** (cut +
render with music and b-roll) → **Style Lab** (turn a reference edit into a
template).

## Style Lab — clone an editing style from reference clips

Turn edits you like into reusable templates:

    python main.py frames <video> --mode scene --grid 3x4   # extract frames
    python main.py style <video> --name my_style            # analyze + draft template

- `frames` extracts stills (`uniform` sampling or `scene`-change detection)
  into `data/frames/<stem>/` with contact sheets + a timestamped manifest.
- `style` runs pixel analysis over the frames (band geometry, hook/caption/CTA
  positions and colors, keyword-highlight detection), writes a
  `style_report.json` and a **draft template** into `templates/`.
- The draft is a starting point: tune sizes/margins, then export with
  `--template <name>`. `templates/abu_lahya.json` was authored this way.
- Templates support a `cta` block (static call-to-action line, e.g.
  "Follow for more!") and `anchor: "band"` text positioning relative to the
  video band in letterbox/square-band layouts.

## Configuration

- `config.json` — models, thresholds, default template, paths.
  Notable transcription settings:
  - `language` pinned to `"en"` (stops autodetect wobble on accented speech)
  - `initial_prompt` vocabulary biasing — edit this list to match your
    channel's topics/names to cut phonetic mishearings
  - `low_confidence_threshold` — words below this probability get LLM-corrected
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
