"""
Generates buyer-walkthrough.mp4 and supplier-walkthrough.mp4 — short,
procedurally-animated videos that simulate a real user clicking through the
same 4-step mock screens shown on the interactive /demo/ page: a cursor
moves to each field, text types in, buttons show a click ripple, progress
bars fill and checklist items tick off.

No screen-recording tool is available in this environment, so each frame is
drawn with Pillow (matching the site's navy/accent brand colors) and piped
as raw RGB24 into a bundled ffmpeg binary (via imageio-ffmpeg) to encode a
real H.264 .mp4.

Run: python scripts/generate_walkthrough_videos.py
Output: homepage/static/videos/buyer-walkthrough.mp4
        homepage/static/videos/supplier-walkthrough.mp4
"""
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg

# ---------------------------------------------------------------- constants

WIDTH, HEIGHT = 1280, 720
FPS = 30
SCENE_SECONDS = 5.0
FADE_SECONDS = 0.45

NAVY_900 = (17, 53, 92)
WHITE = (255, 255, 255)
GRAY_50 = (249, 250, 251)
GRAY_100 = (243, 244, 246)
GRAY_300 = (209, 213, 219)
GRAY_400 = (156, 163, 175)
GRAY_500 = (107, 114, 128)
GRAY_600 = (75, 85, 99)
ACCENT_500 = (245, 121, 33)
ACCENT_600 = (214, 101, 18)
ACCENT_50 = (254, 237, 224)
GREEN_500 = (34, 197, 94)
GREEN_50 = (240, 253, 244)
GREEN_700 = (21, 128, 61)

FONTS_DIR = Path(r"C:\Windows\Fonts")


def font(path, size):
    return ImageFont.truetype(str(FONTS_DIR / path), size)


F_H1 = font("segoeuib.ttf", 40)
F_BODY = font("segoeui.ttf", 20)
F_BODY_B = font("segoeuib.ttf", 20)
F_SMALL = font("segoeui.ttf", 16)
F_SMALL_B = font("segoeuib.ttf", 16)
F_LABEL = font("segoeuib.ttf", 14)
F_EYEBROW = font("segoeuib.ttf", 16)

OUT_DIR = Path(__file__).resolve().parent.parent / "homepage" / "static" / "videos"


# --------------------------------------------------------------- animation

def rng(t, a, b):
    """Map t linearly from [a, b] to [0, 1], clamped outside that range."""
    if b <= a:
        return 1.0 if t >= b else 0.0
    return max(0.0, min(1.0, (t - a) / (b - a)))


def ease(t):
    return t * t * (3 - 2 * t)  # smoothstep


def lerp_pt(p0, p1, t):
    return (p0[0] + (p1[0] - p0[0]) * t, p0[1] + (p1[1] - p0[1]) * t)


def cursor_pos(t, waypoints):
    """waypoints: list of (t, (x, y)), sorted by t. Eased interpolation between them."""
    if t <= waypoints[0][0]:
        return waypoints[0][1]
    for (t0, p0), (t1, p1) in zip(waypoints, waypoints[1:]):
        if t0 <= t <= t1:
            return lerp_pt(p0, p1, ease(rng(t, t0, t1)))
    return waypoints[-1][1]


def typed(text, t):
    """Reveal `text` progressively as t goes 0 -> 1; show a caret while typing."""
    t = max(0.0, min(1.0, t))
    n = int(round(len(text) * ease(t)))
    shown = text[:n]
    if 0.0 < t < 1.0:
        shown += "|"
    return shown


# ------------------------------------------------------------------ drawing

def base_canvas():
    img = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, WIDTH, 150], fill=NAVY_900)
    return img, draw


def header(draw, eyebrow, title, step_label):
    draw.text((64, 40), eyebrow.upper(), font=F_EYEBROW, fill=(220, 230, 240))
    draw.text((64, 68), title, font=F_H1, fill=WHITE)
    w = draw.textlength(step_label, font=F_BODY_B)
    draw.rounded_rectangle([WIDTH - 64 - w - 32, 50, WIDTH - 64, 90], radius=20, fill=ACCENT_500)
    draw.text((WIDTH - 64 - w - 16, 60), step_label, font=F_BODY_B, fill=WHITE)


def card(draw, x, y, w, h, fill=WHITE, outline=GRAY_300):
    draw.rounded_rectangle([x, y, x + w, y + h], radius=14, fill=fill, outline=outline, width=2)


def badge(draw, x, y, text, fill, text_color):
    w = draw.textlength(text, font=F_SMALL_B)
    pad_x, pad_y = 14, 7
    draw.rounded_rectangle([x, y, x + w + pad_x * 2, y + 16 + pad_y * 2], radius=14, fill=fill)
    draw.text((x + pad_x, y + pad_y), text, font=F_SMALL_B, fill=text_color)
    return x + w + pad_x * 2


def field_box(draw, x, y, w, label):
    draw.text((x, y), label.upper(), font=F_LABEL, fill=GRAY_500)
    box_y = y + 22
    draw.rounded_rectangle([x, box_y, x + w, box_y + 44], radius=8, fill=GRAY_50, outline=GRAY_300, width=2)
    return box_y


def field_text(draw, x, y, w, text):
    box_y = y + 22
    draw.text((x + 14, box_y + 11), text, font=F_BODY, fill=NAVY_900)


def button(draw, x, y, w, h, text, primary=True, pressed=False):
    if primary:
        fill = ACCENT_600 if pressed else ACCENT_500
        outline, text_color = None, WHITE
    else:
        fill = ACCENT_50 if pressed else WHITE
        outline, text_color = GRAY_300, NAVY_900
    draw.rounded_rectangle([x, y, x + w, y + h], radius=8, fill=fill, outline=outline, width=2 if outline else 0)
    tw = draw.textlength(text, font=F_BODY_B)
    draw.text((x + (w - tw) / 2, y + (h - 20) / 2), text, font=F_BODY_B, fill=text_color)


def progress_bar(draw, x, y, w, percent, label_left, label_right):
    draw.text((x, y), label_left, font=F_BODY_B, fill=NAVY_900)
    rw = draw.textlength(label_right, font=F_SMALL)
    draw.text((x + w - rw, y + 2), label_right, font=F_SMALL, fill=GRAY_500)
    bar_y = y + 34
    draw.rounded_rectangle([x, bar_y, x + w, bar_y + 10], radius=5, fill=GRAY_100)
    draw.rounded_rectangle([x, bar_y, x + w * percent / 100, bar_y + 10], radius=5, fill=ACCENT_500)


def checklist(draw, x, y, items):
    for i, (text, state, pulse) in enumerate(items):
        cy = y + i * 34
        color = {"done": GREEN_500, "active": ACCENT_500, "todo": GRAY_300}[state]
        if state == "active" and pulse is not None:
            glow = 0.5 + 0.5 * math.sin(pulse * 2 * math.pi * 5)
            color = tuple(int(c + (255 - c) * 0.35 * glow) for c in color)
        draw.ellipse([x, cy, x + 12, cy + 12], fill=color)
        draw.text((x + 24, cy - 4), text, font=F_BODY, fill=GRAY_600 if state != "todo" else GRAY_400)


def cursor_overlay(img, pos, ripple_t=None):
    """Composite a pointer cursor (and an optional click ripple) onto img at pos."""
    x, y = pos
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    od = ImageDraw.Draw(overlay)
    if ripple_t is not None:
        r = 8 + ripple_t * 26
        alpha = int(170 * (1 - ripple_t))
        od.ellipse([x - r, y - r, x + r, y + r], fill=(245, 121, 33, max(0, alpha)))
    pts = [(x, y), (x, y + 17), (x + 4, y + 13), (x + 7, y + 20),
           (x + 10, y + 18), (x + 7, y + 11), (x + 14, y + 11)]
    od.polygon(pts, fill=(255, 255, 255, 255), outline=(17, 53, 92, 255))
    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


# -------------------------------------------------------------- scene defs
# Each scene function takes t in [0, 1] (progress through the scene) and
# returns a fully rendered frame, including the animated cursor.

def scene_buyer_1(t):
    img, draw = base_canvas()
    header(draw, "Buyer Walkthrough", "Post your RFQ", "Step 1 of 4")
    card(draw, 64, 190, WIDTH - 128, 430)

    f1 = field_box(draw, 100, 230, 500, "Part name")
    f2 = field_box(draw, 640, 230, 500, "Process")
    f3 = field_box(draw, 100, 320, 500, "Quantity")
    f4 = field_box(draw, 640, 320, 500, "Drawing")

    field_text(draw, 100, 230, 500, typed("Bracket, Motor Mount — Rev C", rng(t, 0.05, 0.22)))
    field_text(draw, 640, 230, 500, typed("CNC Machining · Aluminium 6061", rng(t, 0.26, 0.42)))
    field_text(draw, 100, 320, 500, typed("250 units", rng(t, 0.46, 0.58)))
    if t >= 0.66:
        # small file icon (a page with a folded corner), drawn with primitives
        # rather than an emoji glyph, which this font can't render reliably.
        ix, iy = 654, f4 + 13
        draw.rectangle([ix, iy, ix + 14, iy + 18], outline=ACCENT_500, width=2, fill=WHITE)
        draw.line([ix + 9, iy, ix + 14, iy + 5], fill=ACCENT_500, width=2)
        field_text(draw, 684, 320, 466, typed("bracket_motor_mount_revC.step", rng(t, 0.66, 0.80)))

    draw.text((100, 420), "Buyers share drawings, specs and quantity in one simple form —", font=F_BODY, fill=GRAY_600)
    draw.text((100, 450), "no back-and-forth emails to get a requirement in front of manufacturers.", font=F_BODY, fill=GRAY_600)

    pressed = 0.90 <= t <= 0.97
    button(draw, 100, 530, 220, 48, "Publish RFQ", pressed=pressed)

    cursor = cursor_pos(t, [
        (0.00, (40, 170)), (0.05, (350, 274)), (0.23, (350, 274)),
        (0.26, (890, 274)), (0.43, (890, 274)),
        (0.46, (350, 364)), (0.59, (350, 364)),
        (0.63, (700, 364)), (0.81, (700, 364)),
        (0.85, (210, 554)), (1.00, (210, 554)),
    ])
    ripple = rng(t, 0.90, 0.98) if 0.90 <= t <= 0.98 else None
    return cursor_overlay(img, cursor, ripple)


def scene_buyer_2(t):
    img, draw = base_canvas()
    header(draw, "Buyer Walkthrough", "Compare quotes side by side", "Step 2 of 4")
    card(draw, 64, 190, WIDTH - 128, 430)

    cols = [100, 460, 620, 780, 900]
    for cx, h in zip(cols, ["Supplier", "Unit price", "Lead time", "Rating", ""]):
        draw.text((cx, 220), h.upper(), font=F_LABEL, fill=GRAY_400)
    draw.line([100, 250, WIDTH - 100, 250], fill=GRAY_100, width=2)

    rows = [
        ("Precision Tooling Co.", "₹412", "12 days", "4.8 ★", "Best value", GREEN_50, GREEN_700, 0.10),
        ("Apex Machine Works", "₹389", "18 days", "4.5 ★", "Lowest price", GRAY_100, GRAY_600, 0.32),
        ("Shree Metal Fab", "₹445", "9 days", "4.9 ★", "Fastest", ACCENT_50, (180, 83, 9), 0.54),
    ]
    for i, (name, price, lead, rating, tag, tagfill, tagcolor, reveal_at) in enumerate(rows):
        if t < reveal_at:
            continue
        ry = 270 + i * 60
        draw.text((cols[0], ry), name, font=F_BODY_B, fill=NAVY_900)
        draw.text((cols[1], ry), price, font=F_BODY, fill=GRAY_600)
        draw.text((cols[2], ry), lead, font=F_BODY, fill=GRAY_600)
        draw.text((cols[3], ry), rating, font=F_BODY, fill=GRAY_600)
        badge(draw, cols[4], ry - 2, tag, tagfill, tagcolor)

    pressed = 0.87 <= t <= 0.95
    button(draw, 100, 540, 340, 48, "Award to Precision Tooling Co.", pressed=pressed)

    cursor = cursor_pos(t, [
        (0.00, (1000, 165)), (0.12, (950, 280)), (0.30, (950, 280)),
        (0.35, (950, 340)), (0.52, (950, 340)),
        (0.57, (950, 400)), (0.72, (950, 400)),
        (0.78, (270, 564)), (1.00, (270, 564)),
    ])
    ripple = rng(t, 0.87, 0.96) if 0.87 <= t <= 0.96 else None
    return cursor_overlay(img, cursor, ripple)


def scene_buyer_3(t):
    img, draw = base_canvas()
    header(draw, "Buyer Walkthrough", "Confirm the order", "Step 3 of 4")
    card(draw, 64, 190, WIDTH - 128, 430)
    card(draw, 100, 240, WIDTH - 200, 160, fill=GRAY_50)
    a = ease(rng(t, 0.0, 0.3))
    draw.text((124, 266), "ORD-10482 · Bracket, Motor Mount — Rev C", font=F_BODY_B, fill=NAVY_900)
    draw.text((124, 300), "Precision Tooling Co. · 250 units · ₹103,000", font=F_BODY, fill=GRAY_600)
    draw.text((124, 330), "Digital PO generated · Payment terms confirmed with the supplier", font=F_SMALL, fill=GRAY_400)
    if t >= 0.35:
        badge(draw, WIDTH - 100 - 180, 266, "PO Confirmed", GREEN_50, GREEN_700)
    draw.text((100, 440), "Awarding a quote generates a digital PO automatically and locks the", font=F_BODY, fill=GRAY_600)
    draw.text((100, 470), "approved drawing to the order.", font=F_BODY, fill=GRAY_600)
    return img


def scene_buyer_4(t):
    img, draw = base_canvas()
    header(draw, "Buyer Walkthrough", "Track to delivery", "Step 4 of 4")
    card(draw, 64, 190, WIDTH - 128, 430)
    percent = 72 * ease(rng(t, 0.10, 0.55))
    progress_bar(draw, 100, 230, WIDTH - 200, percent, "Production progress", "Stage 3 of 4 · Finishing")
    checklist(draw, 100, 330, [
        ("Materials sourced — 3 Jun", "done" if t > 0.08 else "todo", None),
        ("Machining complete — 9 Jun", "done" if t > 0.32 else "todo", None),
        ("Finishing & QC — in progress", "active" if t > 0.55 else "todo", t),
        ("Shipped & delivered", "todo", None),
    ])
    return img


def scene_supplier_1(t):
    img, draw = base_canvas()
    header(draw, "Supplier Walkthrough", "New RFQs matched to you", "Step 1 of 4")
    card(draw, 64, 190, WIDTH - 128, 430)

    cols = [100, 420, 650, 760, 900]
    for cx, h in zip(cols, ["Part", "Process", "Qty", "Match", ""]):
        draw.text((cx, 220), h.upper(), font=F_LABEL, fill=GRAY_400)
    draw.line([100, 250, WIDTH - 100, 250], fill=GRAY_100, width=2)

    rows = [
        ("Bracket, Motor Mount — Rev C", "CNC · Aluminium 6061", "250", "92% match", GREEN_50, GREEN_700, 0.10),
        ("Enclosure, IP65 Panel", "Sheet Metal · SS304", "80", "81% match", ACCENT_50, (180, 83, 9), 0.36),
    ]
    for i, (name, proc, qty, match, mfill, mcolor, reveal_at) in enumerate(rows):
        if t < reveal_at:
            continue
        ry = 270 + i * 60
        draw.text((cols[0], ry), name, font=F_BODY_B, fill=NAVY_900)
        draw.text((cols[1], ry), proc, font=F_BODY, fill=GRAY_600)
        draw.text((cols[2], ry), qty, font=F_BODY, fill=GRAY_600)
        badge(draw, cols[3], ry - 2, match, mfill, mcolor)
        quote_color = ACCENT_600 if (i == 0 and t >= 0.80) else ACCENT_500
        draw.text((cols[4], ry), "Quote →", font=F_BODY_B, fill=quote_color)

    draw.text((100, 440), "Manufacturers only see requirements that match their process,", font=F_BODY, fill=GRAY_600)
    draw.text((100, 470), "material and capacity — with a match score so you know what's worth quoting.", font=F_BODY, fill=GRAY_600)

    cursor = cursor_pos(t, [
        (0.00, (1000, 165)), (0.12, (950, 280)), (0.32, (950, 280)),
        (0.37, (950, 340)), (0.56, (950, 340)),
        (0.60, (935, 280)), (1.00, (935, 280)),
    ])
    ripple = rng(t, 0.80, 0.90) if 0.80 <= t <= 0.90 else None
    return cursor_overlay(img, cursor, ripple)


def scene_supplier_2(t):
    img, draw = base_canvas()
    header(draw, "Supplier Walkthrough", "Submit your quote", "Step 2 of 4")
    card(draw, 64, 190, WIDTH - 128, 430)

    field_box(draw, 100, 230, 320, "Unit price")
    field_box(draw, 460, 230, 320, "Lead time")
    field_box(draw, 820, 230, 320, "Tooling cost")
    field_text(draw, 100, 230, 320, typed("₹412", rng(t, 0.06, 0.22)))
    field_text(draw, 460, 230, 320, typed("12 days", rng(t, 0.28, 0.44)))
    field_text(draw, 820, 230, 320, typed("₹0 (amortised)", rng(t, 0.50, 0.66)))

    draw.text((100, 330), "Respond with price, lead time and tooling cost in one standard", font=F_BODY, fill=GRAY_600)
    draw.text((100, 360), "template — no reformatting someone else's spreadsheet.", font=F_BODY, fill=GRAY_600)

    pressed = 0.88 <= t <= 0.96
    button(draw, 100, 530, 180, 48, "Send quote", pressed=pressed)

    cursor = cursor_pos(t, [
        (0.00, (40, 170)), (0.06, (260, 274)), (0.23, (260, 274)),
        (0.28, (620, 274)), (0.45, (620, 274)),
        (0.50, (980, 274)), (0.67, (980, 274)),
        (0.72, (190, 554)), (1.00, (190, 554)),
    ])
    ripple = rng(t, 0.88, 0.97) if 0.88 <= t <= 0.97 else None
    return cursor_overlay(img, cursor, ripple)


def scene_supplier_3(t):
    img, draw = base_canvas()
    header(draw, "Supplier Walkthrough", "You're awarded the order", "Step 3 of 4")
    card(draw, 64, 190, WIDTH - 128, 430)
    card(draw, 100, 240, WIDTH - 200, 160, fill=GRAY_50)
    draw.text((124, 266), "ORD-10482 · Bracket, Motor Mount — Rev C", font=F_BODY_B, fill=NAVY_900)
    draw.text((124, 300), "250 units · ₹103,000 · Due in 12 days", font=F_BODY, fill=GRAY_600)
    draw.text((124, 330), "Approved drawing locked to the order · Payment terms confirmed with the buyer", font=F_SMALL, fill=GRAY_400)
    if t >= 0.35:
        badge(draw, WIDTH - 100 - 150, 266, "Order won", GREEN_50, GREEN_700)
    draw.text((100, 440), "Once a buyer awards your quote, you get a confirmed PO with terms", font=F_BODY, fill=GRAY_600)
    draw.text((100, 470), "locked in, ready to go into production.", font=F_BODY, fill=GRAY_600)
    return img


def scene_supplier_4(t):
    img, draw = base_canvas()
    header(draw, "Supplier Walkthrough", "Deliver & get paid", "Step 4 of 4")
    card(draw, 64, 190, WIDTH - 128, 430)
    percent = 72 * ease(rng(t, 0.08, 0.45))
    progress_bar(draw, 100, 230, WIDTH - 200, percent, "Production progress", "Stage 3 of 4 · Finishing")
    draw.text((100, 330), "Update production status, upload QC reports and mark the shipment —", font=F_BODY, fill=GRAY_600)
    draw.text((100, 360), "payment follows the terms you agreed with the buyer.", font=F_BODY, fill=GRAY_600)

    pressed = 0.86 <= t <= 0.95
    button(draw, 100, 420, 320, 48, "Upload QC report & mark shipped", primary=False, pressed=pressed)

    cursor = cursor_pos(t, [
        (0.00, (40, 170)), (0.50, (40, 170)),
        (0.55, (260, 444)), (1.00, (260, 444)),
    ])
    ripple = rng(t, 0.86, 0.96) if 0.86 <= t <= 0.96 else None
    return cursor_overlay(img, cursor, ripple)


BUYER_SCENES = [scene_buyer_1, scene_buyer_2, scene_buyer_3, scene_buyer_4]
SUPPLIER_SCENES = [scene_supplier_1, scene_supplier_2, scene_supplier_3, scene_supplier_4]


# ---------------------------------------------------------------- encoding

def render_frames(scenes):
    frames_per_scene = int(SCENE_SECONDS * FPS)
    fade_frames = int(FADE_SECONDS * FPS)
    prev_last_frame = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    for scene_fn in scenes:
        frame = None
        for f in range(frames_per_scene):
            t = f / (frames_per_scene - 1) if frames_per_scene > 1 else 1.0
            frame = scene_fn(t)
            if f < fade_frames:
                blend_t = (f + 1) / fade_frames
                frame = Image.blend(prev_last_frame, frame, blend_t)
            yield frame
        prev_last_frame = frame


def encode(scenes, out_path):
    ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
    cmd = [
        ffmpeg_exe, "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{WIDTH}x{HEIGHT}", "-r", str(FPS),
        "-i", "-",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        "-crf", "20", str(out_path),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    n = 0
    for frame in render_frames(scenes):
        proc.stdin.write(frame.tobytes())
        n += 1
    proc.stdin.close()
    err = proc.stderr.read().decode(errors="ignore")
    code = proc.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg failed ({code}) for {out_path}:\n{err[-2000:]}")
    print(f"wrote {out_path} ({n} frames, {n / FPS:.1f}s)")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    encode(BUYER_SCENES, OUT_DIR / "buyer-walkthrough.mp4")
    encode(SUPPLIER_SCENES, OUT_DIR / "supplier-walkthrough.mp4")


if __name__ == "__main__":
    main()
