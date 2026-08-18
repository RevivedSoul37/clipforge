# ClipForge — Full Project Blueprint

> NOTE (2026-08-18): this is the **original planning snapshot** kept for reference.
> The project has since evolved (web UI in `server.py` + `web/`, cut manifest,
> raw-clip downscaling, preview endpoint). For the current truth see
> `Architecture.md` and `Memory.md`.

---

# PRD.md — ClipForge (AI-Powered Auto Clipper)

## What to Build
A local desktop pipeline that takes a raw long-form video (gaming stream, podcast, vlog) and outputs short, auto-edited, caption-ready clips — with zero manual editing.

Flow: Raw video → Transcribe (Whisper) → LLM picks highlight moments → ffmpeg cuts clips → Template engine applies auto-edit (crop, captions, zoom, transitions, intro/outro) → Export ready-to-post vertical/horizontal clips.

## Targeted Users
- Solo content creators / streamers who record long sessions but don't have time to edit
- Faceless channel operators who need a repeatable clip pipeline
- Munavar himself, first, as the primary user and test case

## Core Features (v1)
1. Import a video file (local upload)
2. Auto-transcribe with timestamps (Whisper, local, runs on his RTX 5070 Ti)
3. LLM scans transcript and scores/selects highlight-worthy segments (funny, exciting, insightful, punchline, etc.) with start/end timestamps and a reason tag
4. User can review/approve/edit the LLM's suggested clip list before rendering
5. ffmpeg cuts approved segments
6. Auto-edit templates applied to each clip:
   - Aspect ratio conversion (16:9 → 9:16 or 1:1)
   - Auto captions burned in (styled, from Whisper word timestamps)
   - Basic zoom/pan or crop-to-face (optional, v2)
   - Intro/outro stinger, watermark (optional, template-based)
7. Batch export all approved clips to an output folder

## Out of Scope (v1)
- Cloud rendering / hosting
- Auto-posting to social platforms (v2+)
- Multi-camera editing
- Real-time/live clipping while streaming

## Success Criteria
- Feed a 1-hour raw video in, get 5-10 usable vertical clips out with captions, with minimal manual review time (<10 min review per video)

---

# Architecture.md — ClipForge

## App Flow
1. **Input** — user drops a video file into `/input`
2. **Transcription Stage** — `faster-whisper` (local, GPU-accelerated) generates a word-level timestamped transcript, saved as JSON
3. **Highlight Selection Stage** — transcript JSON sent to an LLM (local via Ollama, or API) with a scoring prompt → returns a list of candidate clips: `{start, end, reason, score}`
4. **Review Stage** — simple local web UI (Flask or Streamlit) shows candidate clips with transcript snippet, lets user approve/reject/adjust timestamps
5. **Cutting Stage** — ffmpeg cuts each approved clip from the source video
6. **Auto-Edit Stage** — template engine applies: aspect ratio crop, burned-in captions (from word timestamps), optional overlays — via ffmpeg filters or a Python video lib (moviepy)
7. **Export** — final clips written to `/output`, named and numbered

## Folder Structure
```
clipforge/
├── input/                  # raw source videos
├── output/                 # final rendered clips
├── data/
│   ├── transcripts/        # whisper JSON output per video
│   ├── context/             # per-video context files
│   └── clip_candidates/    # LLM-selected clip lists
├── templates/               # editing templates (JSON configs)
│   ├── vertical_captioned.json
│   └── square_clean.json
├── src/
│   ├── transcribe.py        # whisper wrapper
│   ├── build_context.py     # per-video context builder
│   ├── select_highlights.py # LLM prompt + call + parse
│   ├── cut_clips.py         # ffmpeg cut logic
│   ├── apply_template.py    # auto-edit rendering
│   └── review_ui.py         # local review web app
├── PRD.md / Architecture.md / Rules.md / Phases.md / Design.md / Memory.md
└── main.py                  # orchestrates the pipeline
```

## Tech Stack
- **Transcription:** faster-whisper (local, GPU) — fast, accurate, free
- **LLM (highlight selection):** Ollama with a local model (e.g. Qwen/Llama) to start, free and private; swappable for a hosted API later if quality needs it
- **Video cutting/rendering:** ffmpeg (via subprocess) as the core engine; moviepy only if ffmpeg filters get too complex
- **Review UI:** Streamlit (fastest to build, good for local tools with no coding background needed to maintain)
- **Language:** Python throughout, single environment
- **Storage:** local filesystem only, no database needed for v1 (JSON files are enough)

---

# Rules.md — ClipForge

## Use
- Python for all backend/pipeline logic
- ffmpeg for all cutting/rendering (via subprocess, not a heavy wrapper lib) — it's the most reliable and well-documented
- faster-whisper for transcription (GPU-accelerated, local, free)
- Streamlit for the review UI — minimal code, fast to iterate, no frontend framework needed
- JSON files for intermediate data (transcripts, clip candidates) — no database in v1
- Ollama for local LLM calls — free, private, works offline on his GPU

## Avoid
- Don't reach for a database (Postgres/Mongo) in v1 — folders + JSON are enough for a single-user local tool
- Don't use moviepy for core cutting — it's slower and less reliable than raw ffmpeg; only use it if a specific effect truly needs it
- Don't build a full web frontend (React/Next.js) for the review step — Streamlit is enough and much faster to build/maintain solo
- Don't hardcode API keys or paths — use a `.env` / config file
- Don't try to do real-time/live clipping in v1 — batch processing only

## Error Handling
- Every pipeline stage should fail loudly with a clear message (which file, which stage, what went wrong) — never fail silently
- Whisper/LLM stages should save intermediate output to disk immediately, so a crash later in the pipeline doesn't lose transcription work
- Validate that clip timestamps from the LLM are within the video's actual duration before passing to ffmpeg
- If the LLM returns malformed output, retry once with a stricter prompt, then flag for manual review rather than crashing

## Per-Video Context (required before highlight selection)
- Before the LLM picks highlights, generate a `context.json` for that video: video type (gaming/podcast/vlog/etc.), tone, topic, and target platform
- This context gets passed alongside the transcript into the highlight-selection prompt, so picks are grounded in what the video actually is, not a generic pass
- Save it to `data/context/<video_id>_context.json` so it's reusable if the pipeline reruns

## Self-Explaining Templates
- Every template JSON in `/templates/` must include a `description` field in plain English explaining what the template is for and when to use it
- The agent/LLM should be able to read template descriptions and reason about which template fits a given clip, without the user re-explaining each time

## Boundaries for the Coding Agent
- Build and test one phase at a time (see Phases.md) — do not jump ahead
- Do not introduce new dependencies/libraries without flagging them first
- Keep each script single-purpose (transcribe.py only transcribes, etc.) — no giant do-everything files
- Always update Memory.md after completing a phase or file
- Ask before making architecture-level changes (folder structure, tech stack swaps)

---

# Phases.md — ClipForge

## Phase 1 — Transcription Pipeline
- Set up faster-whisper locally, confirm GPU acceleration works
- Script: input video → word-level timestamped transcript JSON
- Test on one sample video end-to-end

## Phase 2 — Highlight Selection (LLM)
- Set up Ollama with a local model
- Write and test the highlight-scoring prompt against the transcript
- Output: structured JSON list of candidate clips (start, end, reason, score)
- Validate timestamps against video duration

## Phase 3 — Cutting Engine
- ffmpeg wrapper: given approved clip list, cut segments from source video
- Output raw (uncut-edited) clips to `/output/raw`

## Phase 4 — Review UI
- Streamlit app: list candidate clips, show transcript snippet + reason/score
- Approve / reject / manually adjust start-end per clip
- Approved list feeds into Phase 3/5

## Phase 5 — Auto-Edit Templates
- Build first template: vertical (9:16) crop + burned-in captions
- Apply template to cut clips via ffmpeg filters
- Add a second template (square/clean, no captions) to prove the template system is swappable

## Phase 6 — Full Pipeline Integration
- `main.py` runs the whole flow: input → transcript → highlights → review → cut → auto-edit → output
- Batch mode: process a whole folder of videos unattended (skip review step or auto-approve above a score threshold)

## Phase 7 — Polish
- Config file for template/model choices
- Basic logging/progress output
- Error handling pass (per Rules.md)

---

# Design.md — ClipForge

## Scope of Design
This is primarily a local tool with a lightweight review UI (Streamlit), not a public-facing product — so design here is about clarity and speed of use, not marketing polish.

## Review UI Theme
- **Background:** dark mode (#0E0E12) — comfortable for long review sessions
- **Accent color:** electric teal (#2DE1C2) — for approve/select actions
- **Reject/danger:** muted red-orange (#E15554)
- **Text:** off-white (#EDEDED) primary, gray (#9A9AA5) secondary/metadata

## Typography
- UI font: Inter or system default sans-serif — clean, readable, no distraction
- Monospace (e.g. JetBrains Mono) for transcript snippets/timestamps — makes timing easy to scan

## Output Clip Captions (burned-in template)
- Font: bold sans-serif (e.g. Montserrat Bold / Anton) — punchy, readable on mobile at small size
- Caption color: white text with black outline/shadow — works on any background
- Keyword highlight (optional v2): pop one key word per line in the accent teal for emphasis, common in gaming/highlight clips

## Layout Principles
- Review screen: one clip at a time, big video preview, transcript snippet below, approve/reject/edit buttons always visible — minimize clicks per clip since he'll be doing this often

---

# Example Template — templates/vertical_captioned.json

```json
{
  "name": "vertical_captioned",
  "description": "Vertical 9:16 clip with bold burned-in captions, for Shorts/Reels. Use for gaming/hype/reaction moments where readability at small size matters most.",
  "output": {
    "aspect_ratio": "9:16",
    "resolution": "1080x1920"
  },
  "crop": {
    "mode": "center_crop",
    "focus": "auto"
  },
  "captions": {
    "enabled": true,
    "font": "Montserrat-Bold",
    "size": 64,
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline_width": 3,
    "position": "bottom_center",
    "highlight_keyword": {
      "enabled": true,
      "color": "#2DE1C2"
    }
  },
  "intro": { "enabled": false },
  "outro": { "enabled": false },
  "watermark": { "enabled": false }
}
```
