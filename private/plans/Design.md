# Design.md — ClipForge

## Scope of Design
A local tool with a web-based review UI, not a public-facing product — design is about clarity and speed of use, not marketing polish.

## Web UI Theme
- **Background:** dark mode (#0E0E12) — comfortable for long review sessions
- **Accent color:** electric teal (#2DE1C2) — for approve/select actions
- **Reject/danger:** muted red-orange (#E15554)
- **Text:** off-white (#EDEDED) primary, gray (#9A9AA5) secondary/metadata

## Typography
- UI font: Inter or system default sans-serif — clean, readable, no distraction
- Monospace (JetBrains Mono) for transcript snippets/timestamps — makes timing easy to scan

## Output Clip Captions (burned-in template)
- Font: bold sans-serif (Montserrat Bold / Anton) — punchy, readable on mobile at small size
- Caption color: white text with black outline/shadow — works on any background
- Keyword highlight: the longest word per caption line pops in accent teal

## Layout Principles (web UI)
- Sidebar: source video picker + upload, output-template selector with plain-English
  description, highlight settings (min score / max clips), and the three main actions
  (Find highlights / Export approved / Run everything)
- Main column: live pipeline status card — big percent, progress rail with playhead,
  and the 5 stage chips (Transcribe → Context → Find highlights → Cut → Render)
- Review: candidate clips as cards with score, reason, editable start/end, transcript
  snippet, and Approve / Reject / ▶ Preview buttons — preview cuts the exact range
  in-place so approvals happen without leaving the page
- Output gallery: rendered clips inline with download links
- Backend log console with a film-sprocket motif, live-streamed from the server
- Overlay with progress message + Cancel (kills the whole process tree)

## Legacy Streamlit Review UI
`src/review_ui.py` stays as a fallback (`python main.py review`); the web UI is the
primary interface.
