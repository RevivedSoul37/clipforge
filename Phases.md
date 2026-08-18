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
- Output raw (un-edited) clips to `/output/raw`

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
- Batch mode: process a whole folder of videos unattended (skip review or auto-approve above a score threshold)

## Phase 7 — Polish
- Config file for template/model choices
- Basic logging/progress output
- Error handling pass (per Rules.md)
