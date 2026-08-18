"""Phase 5 — Auto-edit template engine.

Applies a template JSON (aspect-ratio crop + burned-in captions) to a raw clip
via ffmpeg filters. Captions are generated as an ASS subtitle file from the word
timestamps in the transcript, sliced to the clip's source time range.

v1 supports: center crop to a target aspect ratio, and burned-in captions with
outline + optional keyword highlight. intro/outro/watermark are defined in the
template schema but disabled in v1 (they raise a clear error if enabled).
"""
import json
import subprocess
import tempfile
from pathlib import Path

from src.config import config

TEMPLATE_DIR = config.root / "templates"
FONT_DIR = config.root / "assets" / "fonts"
AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac")


def load_template(name):
    path = TEMPLATE_DIR / name
    if not path.suffix:
        path = path.with_suffix(".json")
    if not path.exists():
        raise FileNotFoundError(f"Template not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _clip_seed(path: Path):
    import re
    m = re.search(r"clip_(\d+)", path.stem)
    if m:
        return int(m.group(1))
    return sum(path.stem.encode()) % 997


def _has_audio(path: Path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0",
           "-show_entries", "stream=index", "-of", "csv=p=0", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and bool(proc.stdout.strip())
    cmd = ["ffprobe", "-v", "error", "-select_streams", "a:0",
           "-show_entries", "stream=index", "-of", "csv=p=0", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode == 0 and bool(proc.stdout.strip())


def _pick_music(template, seed):
    """Choose a background track from music/ per the template's music block.

    Returns None if music is disabled, the folder is missing/empty, or the
    named track doesn't exist (falls back to rotation in that case)."""
    mus = template.get("music", {}) or {}
    if not mus.get("enabled"):
        return None
    if not config.music_dir.is_dir():
        return None
    tracks = sorted(p for p in config.music_dir.iterdir()
                    if p.is_file() and p.suffix.lower() in AUDIO_EXTS)
    if not tracks:
        return None
    name = (mus.get("track") or "").strip()
    if name:
        for p in tracks:
            if p.name == name or p.stem == name:
                return p
        print(f"[template] music track '{name}' not found in {config.music_dir}; "
              f"falling back to rotation")
    return tracks[seed % len(tracks)]


def _probe_video(path: Path):
    cmd = ["ffprobe", "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}:\n{proc.stderr[-2000:]}")
    w, h = proc.stdout.strip().split(",")
    return int(w), int(h)


def _hex_to_bgr(hex_color):
    hex_color = hex_color.lstrip("#")
    r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
    return f"&H00{b}{g}{r}&"  # ASS colors are &HAABBGGRR


def _hex_to_rgb(hex_color):
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i + 2], 16) for i in (0, 2, 4))


def _lerp_rgb(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _rgb_to_bgr(rgb):
    r, g, b = (f"{v:02X}" for v in rgb)
    return f"&H00{b}{g}{r}&"


def _parse_font(name):
    for suffix in ("-BoldItalic", "-Bold", " Bold"):
        if name.endswith(suffix):
            return name[: -len(suffix)], -1
    return name, 0


def _ass_time(sec):
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _clip_words(transcript, clip_start, clip_end):
    words = []
    for seg in transcript.get("segments", []):
        for w in seg.get("words") or []:
            if w["end"] <= clip_start or w["start"] >= clip_end:
                continue
            words.append({
                "word": w["word"],
                "start": round(w["start"] - clip_start, 3),
                "end": round(w["end"] - clip_start, 3),
            })
    return words


def _group_lines(words, max_words=3, max_gap=1.0):
    lines, current = [], []
    for w in words:
        if current and (len(current) >= max_words or w["start"] - current[-1]["end"] > max_gap):
            lines.append(current)
            current = []
        current.append(w)
    if current:
        lines.append(current)
    return lines


def _ass_escape(text):
    return (text.replace("\\", "\\\\")
                .replace("{", "\\{").replace("}", "\\}")
                .replace("\n", "\\N"))


def _alignment(position, default):
    return {"center": 5, "middle_center": 5,
            "bottom_center": 2, "top_center": 8}.get(position, default)


def _build_ass(lines, template, resx, resy, clip_duration, hook_text=None):
    caps = template.get("captions", {})
    font, bold = _parse_font(caps.get("font", "Arial"))
    size = int(caps.get("size", 64))
    outline = int(caps.get("outline_width", 3))
    primary = _hex_to_bgr(caps.get("color", "#FFFFFF"))
    outline_color = _hex_to_bgr(caps.get("outline_color", "#000000"))
    highlight = caps.get("highlight_keyword", {})
    hl_enabled = bool(highlight.get("enabled"))
    hl_color = _hex_to_bgr(highlight.get("color", "#2DE1C2"))
    cap_align = _alignment(caps.get("position", "bottom_center"), 2)
    if "margin_v" in caps:
        margin_v = int(caps["margin_v"])
    else:
        margin_v = 220 if cap_align == 2 and "9:16" in template["output"]["aspect_ratio"] else 80

    grad = caps.get("gradient", {}) or {}
    grad_enabled = bool(grad.get("enabled"))
    grad_top_rgb = _hex_to_rgb(grad.get("top", "#FFF35C"))
    grad_bottom_rgb = _hex_to_rgb(grad.get("bottom", "#FF9A3D"))
    if grad_enabled:
        primary = _rgb_to_bgr(_lerp_rgb(grad_top_rgb, grad_bottom_rgb, 0.5))

    hook = template.get("hook", {})
    hook_enabled = bool(hook.get("enabled")) and bool(hook_text)
    h_font, h_bold = _parse_font(hook.get("font", "Arial"))
    h_size = int(hook.get("size", 72))
    h_color = _hex_to_bgr(hook.get("color", "#F1EFD5"))
    h_align = _alignment(hook.get("position", "top"), 8)
    h_margin_v = int(hook.get("margin_v", 150))

    styles = [f"Style: Caption,{font},{size},{primary},&H00FFFFFF,{outline_color},"
              f"&H80000000,{bold},0,0,0,100,100,0,0,1,{outline},1,{cap_align},40,40,{margin_v},1"]
    if hook_enabled:
        styles.append(
            f"Style: Hook,{h_font},{h_size},{h_color},&H00FFFFFF,&H00000000,&H00000000,"
            f"{h_bold},0,0,0,100,100,0,0,1,0,0,{h_align},80,80,{h_margin_v},1")

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {resx}
PlayResY: {resy}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{chr(10).join(styles)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    if hook_enabled:
        events.append(
            f"Dialogue: 1,0:00:00.00,{_ass_time(clip_duration)},Hook,,0,0,0,,"
            f"{_ass_escape(hook_text)}")
    for line in lines:
        start = _ass_time(line[0]["start"])
        end = _ass_time(line[-1]["end"])
        if hl_enabled:
            key_index = max(range(len(line)), key=lambda i: line[i]["end"] - line[i]["start"])
            parts = []
            for i, w in enumerate(line):
                word = _ass_escape(w["word"])
                if i == key_index:
                    parts.append(f"{{\\c{hl_color}}}{word}{{\\c{primary}}}")
                else:
                    parts.append(word)
            text = " ".join(parts)
        elif grad_enabled:
            n = len(line)
            parts = []
            for i, w in enumerate(line):
                t = 0.5 if n == 1 else i / (n - 1)
                color = _rgb_to_bgr(_lerp_rgb(grad_top_rgb, grad_bottom_rgb, t))
                parts.append(f"{{\\c{color}}}{_ass_escape(w['word'])}")
            text = " ".join(parts)
        else:
            text = " ".join(_ass_escape(w["word"]) for w in line)
        events.append(f"Dialogue: 0,{start},{end},Caption,,0,0,0,,{text}")
    return header + "\n".join(events) + "\n"


def _video_filters(w, h, template):
    """Return the ffmpeg -vf chain for the template's crop mode."""
    out = template["output"]
    rx, ry = (int(x) for x in out["resolution"].split("x"))
    mode = (template.get("crop", {}) or {}).get("mode", "center_crop")
    bg = (template.get("crop", {}) or {}).get("background", "#000000").lstrip("#")

    if mode == "letterbox":
        return [f"scale={rx}:{ry}:force_original_aspect_ratio=decrease",
                f"pad={rx}:{ry}:(ow-iw)/2:(oh-ih)/2:color=0x{bg}"]

    if mode == "square_band":
        side = min(w, h)
        side -= side % 2
        band = min(rx, ry)
        return [f"crop={side}:{side}:{(w - side) // 2}:{(h - side) // 2}",
                f"scale={band}:{band}",
                f"pad={rx}:{ry}:(ow-iw)/2:(oh-ih)/2:color=0x{bg}"]

    a_w, a_h = (int(x) for x in out["aspect_ratio"].split(":"))
    r = a_w / a_h
    src_r = w / h
    filters = []
    if abs(r - src_r) >= 1e-3:
        if r < src_r:
            cw = round(h * r)
            cw -= cw % 2
            filters.append(f"crop={cw}:{h}:{(w - cw) // 2}:0")
        else:
            ch = round(w / r)
            ch -= ch % 2
            filters.append(f"crop={w}:{ch}:0:{(h - ch) // 2}")
    filters.append(f"scale={rx}:{ry}")
    return filters


def _broll_graph(filters, cues, ass_filter, resx, resy, template):
    """Build a -filter_complex graph: base video -> broll overlays -> captions.

    cues: list of (input_index, local_start, local_end, kind).
    Returns (graph_string, final_video_label)."""
    bb = template.get("broll", {}) or {}
    mode = bb.get("mode", "cutaway")
    pip_scale = float(bb.get("pip_scale", 0.6))
    band = min(resx, resy)

    parts = []
    base = "[0:v]"
    if filters:
        parts.append(f"[0:v]{','.join(filters)}[base]")
        base = "[base]"

    prev = base
    for n, (idx, a, b, kind) in enumerate(cues):
        dur = max(0.5, b - a)
        fade_out = max(a, b - 0.3)
        if mode == "pip":
            box = int(band * pip_scale)
            box -= box % 2
            fit = (f"scale={box}:{box}:force_original_aspect_ratio=decrease,"
                   f"pad={box}:{box}:(ow-iw)/2:(oh-ih)/2")
            x = f"(W-w)/2"
            y = f"(H-{band})/2+({band}-h)/2"
        else:
            fit = (f"scale={band}:{band}:force_original_aspect_ratio=decrease,"
                   f"pad={band}:{band}:(ow-iw)/2:(oh-ih)/2")
            x = "(W-w)/2"
            y = f"(H-{band})/2"
        prep = (f"[{idx}:v]{fit},setsar=1,"
                f"fade=t=in:st={a:.3f}:d=0.3,fade=t=out:st={fade_out:.3f}:d=0.3[br{n}]")
        parts.append(prep)
        out = f"[v{n}]"
        parts.append(
            f"{prev}[br{n}]overlay=x={x}:y={y}:enable='between(t,{a:.3f},{b:.3f})'{out}")
        prev = out

    if ass_filter:
        parts.append(f"{prev}{ass_filter}[vout]")
        prev = "[vout]"
    return ";".join(parts), prev


def apply_template(raw_clip_path, transcript_path, clip_start, clip_end,
                   template_name=None, output_dir=None, hook_text=None,
                   broll_cues=None):
    raw_clip_path = Path(raw_clip_path)
    transcript_path = Path(transcript_path)
    if not raw_clip_path.exists():
        raise FileNotFoundError(f"Raw clip not found: {raw_clip_path}")
    if not transcript_path.exists():
        raise FileNotFoundError(f"Transcript not found: {transcript_path}")

    template = load_template(template_name or config.default_template)
    tname = template["name"]

    if template.get("intro", {}).get("enabled") or template.get("outro", {}).get("enabled") \
            or template.get("watermark", {}).get("enabled"):
        raise NotImplementedError(
            f"Template '{tname}' enables intro/outro/watermark, which are out of scope for v1.")

    out = template["output"]
    resx, resy = (int(x) for x in out["resolution"].split("x"))
    w, h = _probe_video(raw_clip_path)
    filters = _video_filters(w, h, template)

    transcript = json.loads(transcript_path.read_text(encoding="utf-8"))
    clip_duration = max(0.1, clip_end - clip_start)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        caps = template.get("captions", {})
        words = _clip_words(transcript, clip_start, clip_end) if caps.get("enabled") else []
        lines = _group_lines(words, max_words=int(caps.get("max_words", 3))) if words else []
        hook_on = bool(template.get("hook", {}).get("enabled")) and bool(hook_text)
        ass_filter = None
        if lines or hook_on:
            ass_text = _build_ass(lines, template, resx, resy, clip_duration,
                                  hook_text=hook_text if hook_on else None)
            ass_path = tmp / "captions.ass"
            ass_path.write_text(ass_text, encoding="utf-8")
            sub_opts = "captions.ass"
            if FONT_DIR.is_dir():
                import shutil
                for fp in FONT_DIR.glob("*.ttf"):
                    shutil.copy2(fp, tmp / fp.name)
                sub_opts += ":fontsdir=."
            ass_filter = f"subtitles='{sub_opts}'"

        bb_on = bool(template.get("broll", {}).get("enabled", True))
        cues = []
        if bb_on and broll_cues:
            for cue in broll_cues:
                p = Path(cue["file"])
                if not p.exists():
                    print(f"[template] broll file missing, skipping cue: {p}")
                    continue
                local_start = max(0.0, cue["start"] - clip_start)
                local_end = min(clip_duration, cue["end"] - clip_start)
                if local_end <= local_start:
                    continue
                cues.append((p, local_start, local_end, cue["kind"]))

        out_dir = Path(output_dir) if output_dir else config.output_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{raw_clip_path.stem}_{tname}.mp4"

        seed = _clip_seed(raw_clip_path)
        music = _pick_music(template, seed)
        mus = template.get("music", {}) or {}
        volume = float(mus.get("volume", 0.12))

        cmd = ["ffmpeg", "-y", "-i", str(raw_clip_path)]
        cue_inputs = []
        for n, (p, a, b, kind) in enumerate(cues):
            if kind == "image":
                cmd += ["-loop", "1", "-i", str(p)]
            else:
                cmd += ["-stream_loop", "-1", "-i", str(p)]
            cue_inputs.append((n + 1, a, b, kind))
        music_idx = 1 + len(cue_inputs)
        if music is not None:
            cmd += ["-i", str(music)]
            print(f"[template] music: {music.name} (volume {volume})")

        graph_parts = []
        if cues:
            graph, vlabel = _broll_graph(filters, cue_inputs, ass_filter,
                                         resx, resy, template)
            graph_parts.append(graph)
            v_map = vlabel
            print(f"[template] broll: {len(cue_inputs)} cutaway(s)")
        else:
            chain = list(filters)
            if ass_filter:
                chain.append(ass_filter)
            v_map = None
            if chain:
                cmd += ["-vf", ",".join(chain)]

        if music is not None:
            fade_out_start = max(0.0, clip_duration - 2.0)
            graph_parts.append(
                f"[{music_idx}:a]aloop=loop=-1:size=2147483647,"
                f"atrim=0:{clip_duration:.3f},asetpts=PTS-STARTPTS,"
                f"afade=t=in:st=0:d=1.5,afade=t=out:st={fade_out_start:.3f}:d=2,"
                f"volume={volume}[bg]")
            if _has_audio(raw_clip_path):
                graph_parts.append("[0:a][bg]amix=inputs=2:duration=first:normalize=0[aout]")
                a_map = "[aout]"
            else:
                a_map = "[bg]"
        else:
            a_map = "0:a:0?"

        if graph_parts:
            cmd += ["-filter_complex", ";".join(graph_parts)]
        if v_map:
            cmd += ["-map", v_map]
        else:
            cmd += ["-map", "0:v:0"]
        cmd += ["-map", a_map]
        cmd += [
            "-c:v", config.video_codec, "-preset", config.preset, "-crf", str(config.crf),
            "-c:a", config.audio_codec, "-b:a", "192k",
            "-t", f"{clip_duration:.3f}",
            str(out_path),
        ]
        print(f"[template] {tname}: {raw_clip_path.name} -> {out_path.name}")
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp))
        if proc.returncode != 0:
            raise RuntimeError(f"ffmpeg failed for template '{tname}':\n{proc.stderr[-2000:]}")

    return out_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Apply an auto-edit template to a raw clip.")
    parser.add_argument("raw_clip")
    parser.add_argument("transcript")
    parser.add_argument("clip_start", type=float)
    parser.add_argument("clip_end", type=float)
    parser.add_argument("--template")
    parser.add_argument("--hook")
    args = parser.parse_args()
    apply_template(args.raw_clip, args.transcript, args.clip_start, args.clip_end,
                   template_name=args.template, hook_text=args.hook)
