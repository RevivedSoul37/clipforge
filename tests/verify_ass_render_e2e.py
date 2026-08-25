"""End-to-end ASS validation: generate a real .ass from every template, then
run ffmpeg (libass) over a tiny black test pattern to confirm the subtitle
syntax is accepted. Exits non-zero if any template's ASS is rejected."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import apply_template as at
from src import text_layout as tl

TPL_DIR = ROOT / "templates"

HOOK = "THE TRUTH ABOUT 7-FIGURE PRODUCTIVITY"
WORDS = [
    {"word": "the", "start": 0.0, "end": 0.4},
    {"word": "truth", "start": 0.4, "end": 0.9},
    {"word": "about", "start": 0.9, "end": 1.4},
    {"word": "productivity", "start": 1.4, "end": 2.2},
]
LINES = at._group_lines(WORDS, max_words=4)

failures = []
with tempfile.TemporaryDirectory() as td:
    td = Path(td)
    # a 2s black 9:16 source for ffmpeg to burn subtitles onto
    src = td / "src.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        "color=c=black:s=1080x1920:d=2:r=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src),
    ], capture_output=True, text=True, check=True)

    for tp in sorted(TPL_DIR.glob("*.json")):
        try:
            tpl = json.loads(tp.read_text(encoding="utf-8-sig"))
        except Exception as e:
            failures.append(f"{tp.name}: json read fail {e}")
            continue
        try:
            ass_text = at._build_ass(LINES, tpl, 1080, 1920, 2.0,
                                     hook_text=HOOK, band_offset=0,
                                     band_height=1920, debug=False)
        except Exception as e:
            failures.append(f"{tp.name}: _build_ass fail {e}")
            continue
        ass_path = td / f"{tp.stem}.ass"
        ass_path.write_text(ass_text, encoding="utf-8")
        # copy fonts so libass finds them
        for f in at.FONT_DIR.glob("*.ttf"):
            (td / f.name).write_bytes(f.read_bytes())
        out = td / f"{tp.stem}_out.mp4"
        proc = subprocess.run([
            "ffmpeg", "-y", "-i", str(src),
            "-vf", f"subtitles='{ass_path.name}:fontsdir=.'",
            "-t", "2", "-c:v", "libx264", "-preset", "ultrafast",
            "-an", str(out),
        ], capture_output=True, text=True, cwd=str(td))
        ok = proc.returncode == 0 and out.exists() and out.stat().st_size > 0
        status = "OK " if ok else "FAIL"
        print(f"{status} {tp.name}  ({len(ass_text)} bytes ASS)")
        if not ok:
            tail = (proc.stderr or "")[-400:]
            failures.append(f"{tp.name}: ffmpeg rejected ASS\n{tail}")

print()
if failures:
    print(f"FAILURES ({len(failures)}):")
    for f in failures:
        print(" -", f)
    sys.exit(1)
print("ALL TEMPLATES RENDERED SUCCESSFULLY THROUGH FFMPEG/LIBASS")
