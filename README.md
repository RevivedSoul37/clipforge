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

## Transcript email pipeline (default highlight flow)

With `HIGHLIGHT_SOURCE=email` (the default), highlight selection runs over
email instead of the local model:

1. **Analyze** transcribes the source, then emails the cleaned numbered
   transcript to `TRANSCRIPT_RECIPIENT_EMAIL` (+ `TRANSCRIPT_FORWARD_EMAIL`).
   The run finishes right away — no blocked wait.
2. The AI reads the transcript and **replies with a JSON object** (contract is
   printed at the top of the transcript email) listing highlight clips by
   `[S<id>]` segment id, hook, score and reason.
3. ClipForge's background IMAP poller ingests the reply (only mail from
   `HIGHLIGHT_REPLY_SENDER` is trusted) and turns it into the same
   `_candidates.json` the local model produces.
4. The **Approval page** shows the pipeline live: a waiting card while the
   reply is in flight, then the received highlights with Approve / Reject /
   Preview and a **Start clipping** button. A bell in the top bar collects
   "transcript sent" and "highlights received" notifications (+ optional
   browser notifications), and "Check inbox now" forces an immediate poll.

The CLI equivalents are `python main.py select --email <video>` and
`python main.py check-email`.

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
- Templates support an optional `effects` block
  (`"grade": none|warm|cool|punchy|bright` plus `"vignette": 0|0.5`) for
  programmatic color/lighting. Missing block = `none` (non-breaking).
- `templates/full_screen.json` — full-frame 9:16: video covers the whole
  frame (speaker-follow crop when available, else center), hook in the top
  safe zone, captions above the platform-UI safe line, both away from the
  face/bands.

**Pre-export step (web UI):** every Export opens a config modal first — pick
the template (Style Explorer winner is pre-selected when it exists) and edit
instructions for the clips. Instructions persist in campaign settings and are
logged at export; CLI equivalent: `python main.py export <video>
--template <name> --instructions "..."`.

## Style Explorer — auto-explore edit styles with a vision LLM

One command tries many style combinations on your best clip, scores them with
a local vision model, and saves the winner for all approved clips.

Pre-req: `ollama pull qwen2.5vl:7b` (or `qwen2.5vl:3b` on low VRAM; set
`vision.model` accordingly). Explore runs fail fast if the model isn't pulled.

    python main.py explore-style <video> [--brief "no red, warm cinematic look"] \
        [--variants 10] [--probe N] [--auto]

What it does:
1. Picks the highest-scored approved clip as the probe (or `--probe N`).
2. Cuts 3 edge variants: default padding, tight VAD trim, extended hook lead-in.
3. Interprets the style brief into advisory constraints (fonts / banned
   colors / grade direction); LLM failure just means the brief is skipped here
   but still passed to the judge.
4. Generates N seeded style variants (crop, bundled fonts, caption style/
   position/size, hook combo, grade, vignette, music on/off) — deterministic
   per video, and always keeps 2 "safe" variants from your golden templates.
5. Renders each variant as a low-res preview (540x960, crf 28, ultrafast) into
   `data/previews/<stem>/`.
6. Extracts frames per preview and asks the vision LLM to score legibility /
   contrast / style / brief-fit on a JSON rubric.
7. Writes the report to `data/style_explorations/<stem>_exploration.json` and
   the winning style to `templates/<stem>_winner.json`.

Cost: bounded at `explore.max_variants` low-res renders of ONE clip plus the
judge calls. The full-quality render happens once, with the winner:

    python main.py export <video>    # auto-uses <stem>_winner.json if present

The web UI has the same flow: style brief textarea (Settings), an "Explore
styles" button on Exports with live progress, a preview grid with scores and
verdicts, and "Save as campaign style" to make the winner the campaign
template.

## Configuration

- `config.json` — models, thresholds, default template, paths.
  - `vision` — Style Explorer judge: `model` (default `qwen2.5vl:7b`),
    `frames_per_variant`, `temperature`; `base_url` defaults to the Ollama URL
  - `explore` — `max_variants`, `preview_resolution`, `preview_crf`,
    `preview_preset` for the exploration previews
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
- `data/broll/cache/` — stock b-roll fetched per clip from Pexels/Pixabay
- `music/` — background tracks (nasheeds/BGM)

## Layout

- `main.py` — CLI orchestrator
- `server.py` — web backend (Starlette)
- `src/` — pipeline stages
- `web/` — frontend
- `private/` — planning docs and session memory (non-production)
