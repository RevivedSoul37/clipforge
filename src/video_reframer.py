"""Face-follow 9:16 (or square) crop path for a clip.

Samples faces with MediaPipe, sticks to the dominant face, and returns a
smoothed (t, x, y) crop path. FFmpeg gets a piecewise-linear crop expression.
If MediaPipe/OpenCV is missing or no face is found, returns a static center crop.
"""
from pathlib import Path

MAX_KEYFRAMES = 24
SAMPLE_FPS = 2.0
DETECT_WIDTH = 640


def _even(n):
    n = int(round(n))
    return n - (n % 2)


def crop_size(src_w, src_h, aspect_w, aspect_h):
    """Largest even crop (cw, ch) of aspect_w:aspect_h that fits in src."""
    target = aspect_w / aspect_h
    src = src_w / src_h
    if src > target:
        ch = _even(src_h)
        cw = _even(src_h * target)
        if cw > src_w:
            cw = _even(src_w)
    else:
        cw = _even(src_w)
        ch = _even(src_w / target)
        if ch > src_h:
            ch = _even(src_h)
    cw = max(2, min(cw, _even(src_w)))
    ch = max(2, min(ch, _even(src_h)))
    return cw, ch


def center_offset(src_w, src_h, cw, ch):
    return _even((src_w - cw) / 2), _even((src_h - ch) / 2)


def _clamp_offset(x, y, src_w, src_h, cw, ch):
    max_x = max(0, src_w - cw)
    max_y = max(0, src_h - ch)
    x = min(max(0, int(round(x))), max_x)
    y = min(max(0, int(round(y))), max_y)
    return _even(x), _even(y)


def _import_cv():
    try:
        import cv2
        return cv2
    except ImportError:
        print("[reframe] opencv-python-headless not installed; center crop")
        return None


def _import_faces():
    try:
        import mediapipe as mp
        return mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=0.45)
    except Exception as exc:  # noqa: BLE001
        print(f"[reframe] mediapipe unavailable ({exc}); center crop")
        return None


def _pick_face(detections, prev_cx, frame_w):
    """Largest face, with hysteresis toward the previous center."""
    boxes = []
    for det in detections:
        bb = det.location_data.relative_bounding_box
        cx = (bb.xmin + bb.width / 2) * frame_w
        area = max(0.0, bb.width) * max(0.0, bb.height)
        boxes.append((area, cx))
    if not boxes:
        return None
    boxes.sort(reverse=True)
    if prev_cx is None:
        return boxes[0][1]
    # stick to previous speaker unless another face is clearly bigger + closer
    nearest = min(boxes, key=lambda b: abs(b[1] - prev_cx))
    biggest = boxes[0]
    if abs(nearest[1] - prev_cx) < frame_w * 0.18:
        return nearest[1]
    return biggest[1]


def _sample_centers(video_path, sample_fps=SAMPLE_FPS):
    cv2 = _import_cv()
    detector = _import_faces()
    if cv2 is None or detector is None:
        return None, None, None

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        print(f"[reframe] could not open {video_path}")
        return None, None, None
    src_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    src_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    nframes = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = (nframes / fps) if fps > 0 and nframes > 0 else 0.0
    step = max(1, int(round(fps / max(0.5, sample_fps))))
    scale = DETECT_WIDTH / src_w if src_w > DETECT_WIDTH else 1.0

    samples = []
    prev_cx = None
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % step != 0:
                idx += 1
                continue
            t = idx / fps if fps else 0.0
            small = cv2.resize(frame, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA) if scale < 1 else frame
            rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
            result = detector.process(rgb)
            cx = None
            if result.detections:
                cx = _pick_face(result.detections, prev_cx, small.shape[1])
            if cx is not None:
                prev_cx = cx
                samples.append((t, cx / small.shape[1]))
            idx += 1
    finally:
        cap.release()
        try:
            detector.close()
        except Exception:  # noqa: BLE001
            pass
    return samples, (src_w, src_h), duration


def _smooth(samples, duration):
    if not samples:
        return []
    alpha = 0.4
    out = []
    ema = samples[0][1]
    for t, v in samples:
        ema = alpha * v + (1 - alpha) * ema
        out.append((t, ema))
    # hold last value to clip end
    if duration and out[-1][0] < duration - 0.05:
        out.append((duration, out[-1][1]))
    return out


def _simplify(points, max_pts=MAX_KEYFRAMES):
    if len(points) <= max_pts:
        return points
    # keep endpoints + largest x-jumps
    scored = sorted(range(1, len(points) - 1),
                    key=lambda i: abs(points[i][1] - points[i - 1][1]),
                    reverse=True)
    keep = {0, len(points) - 1, *scored[: max_pts - 2]}
    return [p for i, p in enumerate(points) if i in keep]


def _expr_piecewise(times, values):
    """FFmpeg expression: piecewise-linear interpolation of (t → value)."""
    if not times:
        return "0"
    if len(times) == 1:
        return f"{values[0]:.3f}"
    expr = f"{values[-1]:.3f}"
    for i in range(len(times) - 2, -1, -1):
        t0, t1 = times[i], times[i + 1]
        v0, v1 = values[i], values[i + 1]
        dt = max(1e-3, t1 - t0)
        lerp = f"{v0:.3f}+({v1:.3f}-({v0:.3f}))*(t-{t0:.3f})/{dt:.3f}"
        expr = f"if(lt(t,{t1:.3f}),{lerp},{expr})"
    return expr


def compute_crop_path(video_path, cw, ch, src_w=None, src_h=None):
    """Return (x_expr, y_expr, static_x, static_y).

    static_* are set when the path does not move (use a plain crop).
    """
    video_path = Path(video_path)
    samples, size, duration = _sample_centers(video_path)
    if size:
        src_w, src_h = size
    if not src_w or not src_h:
        return None
    cx0, cy0 = center_offset(src_w, src_h, cw, ch)
    if not samples:
        print("[reframe] no faces; center crop")
        return f"{cx0}", f"{cy0}", cx0, cy0

    smoothed = _simplify(_smooth(samples, duration or samples[-1][0]))
    xs, ys = [], []
    times = []
    for t, nx in smoothed:
        face_x = nx * src_w
        x, y = _clamp_offset(face_x - cw / 2, cy0, src_w, src_h, cw, ch)
        times.append(t)
        xs.append(x)
        ys.append(y)

    if max(xs) - min(xs) < 8 and max(ys) - min(ys) < 8:
        return f"{xs[0]}", f"{ys[0]}", xs[0], ys[0]

    print(f"[reframe] {len(times)} crop keyframes over {times[-1]:.1f}s "
          f"(x {min(xs)}-{max(xs)})")
    return _expr_piecewise(times, xs), _expr_piecewise(times, ys), None, None


def crop_filter(video_path, src_w, src_h, aspect="9:16", fallback_center=True):
    """FFmpeg crop filter string following the speaker, or center crop."""
    try:
        aw, ah = (int(x) for x in aspect.split(":"))
    except (ValueError, AttributeError):
        aw, ah = 9, 16
    cw, ch = crop_size(src_w, src_h, aw, ah)
    if cw >= src_w - 2 and ch >= src_h - 2:
        return None
    path = compute_crop_path(video_path, cw, ch, src_w, src_h)
    if path is None and fallback_center:
        x, y = center_offset(src_w, src_h, cw, ch)
        return f"crop={cw}:{ch}:{x}:{y}"
    if path is None:
        return None
    x_expr, y_expr, sx, sy = path
    if sx is not None:
        return f"crop={cw}:{ch}:{sx}:{sy}"
    return f"crop={cw}:{ch}:{_esc_expr(x_expr)}:{_esc_expr(y_expr)}"


def _esc_expr(expr):
    """Escape :, , and \\ so the expression survives an ffmpeg filtergraph."""
    return str(expr).replace("\\", "\\\\").replace(":", "\\:").replace(",", "\\,")


def subject_box(video_path, src_w=None, src_h=None, out_w=1080, out_h=1920):
    """Return a BoundingBox in the OUTPUT canvas space covering where the
    dominant face appears, or None if no face / no mediapipe.

    Used by the text layout engine to penalize text over the subject. The box
    is a coarse, conservative region (face + a margin) so the layout engine
    treats a generous area as 'subject' and tries to route text around it."""
    video_path = Path(video_path)
    try:
        samples, size, _ = _sample_centers(video_path)
    except Exception:
        return None
    if size:
        src_w, src_h = size
    if not src_w or not src_h or not samples:
        return None
    # median normalized face-x across the clip (the dominant speaker)
    nxs = [nx for _, nx in samples]
    nx = sorted(nxs)[len(nxs) // 2]
    # Map face center into the OUTPUT canvas. The crop already follows the
    # face, so in the cropped output frame the face sits near horizontal center.
    # Vertical: faces are usually in the upper-middle third of the frame.
    face_w_out = int(out_w * 0.34)   # generous face+head width
    face_h_out = int(out_h * 0.30)   # face+upper-torso height
    # face-x in output: crop centers on the face -> ~center horizontally
    cx_out = int(nx * out_w)
    cx_out = max(face_w_out // 2, min(out_w - face_w_out // 2, cx_out))
    # vertical: assume face sits ~25%-55% down the output frame
    cy_out = int(out_h * 0.40)
    box_x = cx_out - face_w_out // 2
    box_y = cy_out - face_h_out // 2
    # clamp to canvas
    box_x = max(0, min(out_w - face_w_out, box_x))
    box_y = max(0, min(out_h - face_h_out, box_y))
    # return as a lightweight object the layout engine understands
    class _Box:
        pass
    b = _Box()
    b.x, b.y, b.width, b.height = box_x, box_y, face_w_out, face_h_out
    return b
