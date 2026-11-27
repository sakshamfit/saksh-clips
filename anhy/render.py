"""Turns a time range into a vertical clip: 9:16 layout, bouncy word captions, hook title, cover, and signal-aware editing."""
import math
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from . import branding, config, roman
from .media import NO_WINDOW, run

W, H = config.OUT_W, config.OUT_H

# ASS colours are &HBBGGRR. `size` is the ASS font size (line height); _Font turns it into real pixels.
STYLES = {
    "bounce": {"label": "Bounce", "font": "Poppins ExtraBold", "size": 132, "base": "FFFFFF", "active": "00F0FF",
               "strong": "FFFFFF", "outline": 7, "pop": 112, "upper": True, "per": 3},
    "hype": {"label": "Hype", "font": "Anton", "size": 152, "base": "FFFFFF", "active": "3C3CFF",
             "strong": "00D7FF", "outline": 7, "pop": 114, "upper": True, "per": 2},
    "clean": {"label": "Clean", "font": "Poppins ExtraBold", "size": 100, "base": "FFFFFF", "active": "6BD7FF",
              "strong": "FFFFFF", "outline": 5, "pop": 106, "upper": False, "per": 4},
}
HOOK = {"font": "Poppins ExtraBold", "size": 96, "ink": "14100E", "box": "FFFFFF", "pad_x": 36, "pad_y": 22, "radius": 26,
        "max_w": 900}
FONT_FILES = {"Poppins ExtraBold": "Poppins-ExtraBold.ttf", "Anton": "Anton-Regular.ttf", "Archivo Black": "ArchivoBlack-Regular.ttf"}
CAP_MAX_W = 960   # captions never run wider than this, whatever is said

EDIT_STYLES = ("off", "balanced", "pro", "energetic")
EDIT_STYLE_META = {
    "off": {"label": "Off", "hint": "Keep original framing, color, and audio without automatic edits."},
    "balanced": {"label": "Balanced", "hint": "Studio voice mastering, film clarity grade, subtle dolly motion, and clean punch-ins."},
    "pro": {"label": "Pro", "hint": "Dead-air jump-cuts, multi-cam framing shifts, snappy punch-ins, climax impact hit, film grade, studio voice & progress bar."},
    "energetic": {"label": "Energetic", "hint": "Tightest jump-cut pacing, bold 1.38x snap zooms, climax micro-shake & flash, punchy film grade, and full cinematic SFX."},
}

_PRESET_CFG = {
    "balanced": {
        "max_beats": 3,
        "max_beats_long": 4,
        "min_gap": 3.8,
        "intro_dur": 1.15,
        "intro_zoom": 1.09,
        "dolly_drift": 0.04,
        "zoom_setup": 1.16,
        "zoom_payoff": 1.24,
        "zoom_reaction": 1.19,
        "zoom_calm": 1.13,
        "hold_setup": 1.30,
        "hold_payoff": 1.60,
        "hold_reaction": 1.35,
        "ease_in": 0.18,
        "ease_out": 0.28,
        "pan_scale": 0.30,
        "jump_cuts": False,
        "cut_min_silence": 0.90,
        "cut_keep_pause": 0.20,
        "cut_alt_zoom": 0.0,
        "impact_shake": 0.0,
        "impact_flash": 0.0,
        "progress_bar": False,
        "caption_scrim": True,
        "contrast": 1.08,
        "saturation": 1.10,
        "brightness": 0.008,
        "sharpen": 0.42,
        "vignette": "PI/6.6",
        "split_tone": True,
        "s_curve": True,
        "studio_voice": True,
        "sfx_gain": 1.05,
        "intro_sound": True,
    },
    "pro": {
        "max_beats": 5,
        "max_beats_long": 6,
        "min_gap": 2.9,
        "intro_dur": 0.95,
        "intro_zoom": 1.13,
        "dolly_drift": 0.06,
        "zoom_setup": 1.22,
        "zoom_payoff": 1.34,
        "zoom_reaction": 1.26,
        "zoom_calm": 1.17,
        "hold_setup": 1.15,
        "hold_payoff": 1.55,
        "hold_reaction": 1.25,
        "ease_in": 0.12,
        "ease_out": 0.22,
        "pan_scale": 0.42,
        "jump_cuts": True,
        "cut_min_silence": 0.72,
        "cut_keep_pause": 0.16,
        "cut_alt_zoom": 0.16,
        "impact_shake": 6.5,
        "impact_flash": 0.06,
        "progress_bar": True,
        "caption_scrim": True,
        "contrast": 1.11,
        "saturation": 1.14,
        "brightness": 0.010,
        "sharpen": 0.58,
        "vignette": "PI/5.8",
        "split_tone": True,
        "s_curve": True,
        "studio_voice": True,
        "sfx_gain": 1.25,
        "intro_sound": True,
    },
    "energetic": {
        "max_beats": 6,
        "max_beats_long": 8,
        "min_gap": 2.3,
        "intro_dur": 0.80,
        "intro_zoom": 1.17,
        "dolly_drift": 0.075,
        "zoom_setup": 1.26,
        "zoom_payoff": 1.38,
        "zoom_reaction": 1.30,
        "zoom_calm": 1.20,
        "hold_setup": 0.95,
        "hold_payoff": 1.45,
        "hold_reaction": 1.15,
        "ease_in": 0.09,
        "ease_out": 0.19,
        "pan_scale": 0.50,
        "jump_cuts": True,
        "cut_min_silence": 0.55,
        "cut_keep_pause": 0.14,
        "cut_alt_zoom": 0.20,
        "impact_shake": 9.5,
        "impact_flash": 0.085,
        "progress_bar": True,
        "caption_scrim": True,
        "contrast": 1.14,
        "saturation": 1.18,
        "brightness": 0.014,
        "sharpen": 0.68,
        "vignette": "PI/5.4",
        "split_tone": True,
        "s_curve": True,
        "studio_voice": True,
        "sfx_gain": 1.40,
        "intro_sound": True,
    },
}


def normalize_edit_style(obj: dict | None) -> str:
    """Resolve `edit_style` while staying backwards compatible with boolean `auto_edit`."""
    if not isinstance(obj, dict):
        return "pro"
    if obj.get("auto_edit") is False:
        return "off"
    raw = obj.get("edit_style")
    if isinstance(raw, str) and raw.strip().lower() in EDIT_STYLES:
        return raw.strip().lower()
    if "auto_edit" in obj:
        return "pro" if bool(obj.get("auto_edit")) else "off"
    return "pro"


class _Font:
    """Measures text exactly as the renderer will draw it, so every word can be placed by hand: the highlighted
    word can grow without pushing its neighbours, and the heading box fits its text."""
    _cache: dict = {}

    def __init__(self, name):
        import struct
        path = Path(__file__).resolve().parent / "fonts" / FONT_FILES.get(name, FONT_FILES["Poppins ExtraBold"])
        d = path.read_bytes()
        tables = {}
        for i in range(struct.unpack(">H", d[4:6])[0]):
            tag, _, off, _ = struct.unpack(">4sIII", d[12 + 16 * i: 28 + 16 * i])
            tables[tag] = off
        self.upm = struct.unpack(">H", d[tables[b"head"] + 18: tables[b"head"] + 20])[0]
        o = tables[b"OS/2"]
        self.asc, self.desc = struct.unpack(">HH", d[o + 74: o + 78])      # what the renderer sizes the font by
        self.cap = struct.unpack(">h", d[o + 88: o + 90])[0] or int(self.upm * 0.7)
        self.path = str(path)
        self._pil = {}

    @classmethod
    def get(cls, name):
        if name not in cls._cache:
            cls._cache[name] = cls(name)
        return cls._cache[name]

    def em(self, size):
        return size * self.upm / (self.asc + self.desc)

    def width(self, text, size):
        """Pixels wide at this ASS size."""
        try:
            from PIL import ImageFont
            if "f" not in self._pil:
                self._pil["f"] = ImageFont.truetype(self.path, 200)
            return self._pil["f"].getlength(text) * self.em(size) / 200
        except Exception:  # noqa: BLE001 - no measuring library: a fair estimate keeps clips rendering
            return len(text) * self.em(size) * 0.62

    def cap_shift(self, size):
        """How far below the anchor the middle of a capital letter sits when text is centred on a point."""
        return ((self.asc - self.desc) / 2 - self.cap / 2) * size / (self.asc + self.desc)


CAPTION_LEAD = 0.0  # measured: word stamps are within 0.15 s of the voice, so no shift is needed


def install_fonts():
    """Copy the bundled caption fonts next to the data, so captions look the same on every machine and server."""
    src = Path(__file__).resolve().parent / "fonts"
    for f in src.glob("*.ttf"):
        if not (config.FONTS_DIR / f.name).exists():
            shutil.copyfile(f, config.FONTS_DIR / f.name)


LAYOUTS = {"blur": "Full frame + blurred fill", "zoom": "Zoomed in + blurred fill", "crop": "Fill the screen (centre crop)",
           "gameplay": "Gameplay fills the screen, facecam in the corner",
           "split": "Streamer on top, gameplay on bottom (half and half)"}


def _ts(t):
    t = max(0.0, t)
    h, m = int(t // 3600), int(t % 3600 // 60)
    return f"{h}:{m:02d}:{t % 60:05.2f}"


def _clean(text):
    return re.sub(r"[\x00-\x1f\x7f]+", " ", re.sub(r"[{}\\]", "", str(text))).strip()


def _key(word):
    return re.sub(r"[^a-z0-9']", "", word.lower())


def _groups(words, per):
    out, cur = [], []
    for w in words:
        if cur and (len(cur) >= per or sum(len(x["w"]) for x in cur) + len(w["w"]) > 16
                    or w["s"] - cur[-1]["e"] > 0.45 or cur[-1]["w"][-1:] in ".?!"):
            out.append(cur)
            cur = []
        cur.append(w)
    if cur:
        out.append(cur)
    return out


def geometry(src_w, src_h, layout):
    """Where the video sits in the 1080x1920 frame, and where hook and captions go."""
    if src_h >= src_w and layout not in ("crop", "gameplay", "split"):
        layout = "crop"  # already vertical or square: nothing to gain from a blurred fill
    if layout == "blur":
        fg_h = min(H, int(round(W * src_h / src_w / 2)) * 2)
    elif layout == "zoom":
        fg_h = 1000
    else:
        fg_h = H
    top = (H - fg_h) // 2
    if layout in ("crop", "gameplay"):
        return layout, fg_h, 300, 1380
    if layout == "split":
        return layout, fg_h, int(H * 0.56), int(H * 0.87)
    return layout, fg_h, max(230, top - 190), min(1640, top + fg_h + 180)


def _bottom_scrim_events(dur: float, cap_y: int) -> list[str]:
    """Silky-smooth feathered dark gradient backdrop behind lower captions so white/highlight text pops cleanly."""
    if dur <= 0.1:
        return []
    span = f"{_ts(0)},{_ts(dur)}"
    top_y = max(int(H * 0.56), int(cap_y) - 340)
    h_span = H - top_y
    if h_span < 80:
        return []
    # 6 softly blurred overlapping gradient layers (smooth ease-in curve, fast libass rasterization)
    steps = 6
    out = []
    for i in range(steps):
        frac = i / steps
        y0 = top_y + int(h_span * frac)
        alpha = f"{int(round(234 - 14 * (frac ** 1.15))):02X}"
        rect = f"m -30 {y0} l {W + 30} {y0} l {W + 30} {H + 30} l -30 {H + 30}"
        out.append(f"Dialogue: 0,{span},Scrim,,0,0,0,,{{\\an7\\pos(0,0)\\c&H000000&\\alpha&H{alpha}&\\blur26\\p1}}{rect}{{\\p0}}")
    return out


def _progress_bar_events(dur: float, style_name: str) -> list[str]:
    """Sleek animated bottom retention progress bar (0% -> 100% over clip duration)."""
    if dur < 1.5:
        return []
    span = f"{_ts(0)},{_ts(dur)}"
    dur_ms = max(200, int(round(dur * 1000)))
    st = STYLES.get(style_name, STYLES["bounce"])
    bar_h = 8
    y0 = H - bar_h
    track = f"m 0 {y0} l {W} {y0} l {W} {H} l 0 {H}"
    fill = f"m 0 0 l {W} 0 l {W} {bar_h} l 0 {bar_h}"
    return [
        f"Dialogue: 5,{span},Scrim,,0,0,0,,{{\\an7\\pos(0,0)\\c&HFFFFFF&\\alpha&HD0&\\p1}}{track}{{\\p0}}",
        f"Dialogue: 6,{span},Scrim,,0,0,0,,{{\\an1\\pos(0,{H})\\c&H{st['active']}&\\alpha&H18&\\fscx1\\fscy100\\t(0,{dur_ms},\\fscx100)\\p1}}{fill}{{\\p0}}",
    ]


def _hook_events(hook, dur, y, edit_style="pro"):
    """The heading: dark text in a crisp white rounded box with snappy spring pop-in and soft shadow."""
    f, size = _Font.get(HOOK["font"]), HOOK["size"]
    words = hook.split()
    if not words:
        return []
    lines = [" ".join(words)]
    if f.width(lines[0], size) > HOOK["max_w"] and len(words) > 1:      # two lines, split where they come out most even
        cut = min(range(1, len(words)), key=lambda i: max(f.width(" ".join(words[:i]), size), f.width(" ".join(words[i:]), size)))
        lines = [" ".join(words[:cut]), " ".join(words[cut:])]
    widest = max(f.width(l, size) for l in lines)
    if widest > HOOK["max_w"]:
        size = max(40, int(size * HOOK["max_w"] / widest))
        widest = max(f.width(l, size) for l in lines)
    line_h = f.em(size) * 1.16
    bw, bh = widest + 2 * HOOK["pad_x"], line_h * len(lines) + 2 * HOOK["pad_y"]
    r = min(HOOK["radius"], bh / 2)
    # Build non-negative (0..bw, 0..bh) vector box so libass \an5\pos(W/2, y) centers & spring-scales symmetrically!
    n = lambda v: f"{v:.0f}"                                                         # noqa: E731
    box = (f"m {n(r)} 0 l {n(bw - r)} 0 b {n(bw)} 0 {n(bw)} 0 {n(bw)} {n(r)} "
           f"l {n(bw)} {n(bh - r)} b {n(bw)} {n(bh)} {n(bw)} {n(bh)} {n(bw - r)} {n(bh)} "
           f"l {n(r)} {n(bh)} b 0 {n(bh)} 0 {n(bh)} 0 {n(bh - r)} "
           f"l 0 {n(r)} b 0 0 0 0 {n(r)} 0")
    hook_end = dur if edit_style == "off" or dur <= 5.5 else min(dur, max(3.2, min(4.6, dur * 0.26)))
    span = f"{_ts(0)},{_ts(hook_end)}"
    fade = "\\fad(90,220)" if hook_end < dur - 0.1 else "\\fad(120,0)"
    spring = "" if edit_style == "off" else "\\fscx84\\fscy84\\t(0,110,\\fscx104\\fscy104)\\t(110,200,\\fscx100\\fscy100)"
    ev = []
    if edit_style != "off":
        # Soft drop shadow behind hook card
        ev.append(f"Dialogue: 2,{span},Hook,,0,0,0,,{{\\an5\\pos({W // 2},{y + 10:.0f}){fade}{spring}\\c&H000000&\\alpha&H94&\\blur10\\p1}}{box}{{\\p0}}")
    ev.append(f"Dialogue: 3,{span},Hook,,0,0,0,,{{\\an5\\pos({W // 2},{y:.0f}){fade}{spring}\\c&H{HOOK['box']}&\\p1}}{box}{{\\p0}}")
    for i, line in enumerate(lines):
        cy = y - line_h * (len(lines) - 1) / 2 + i * line_h - f.cap_shift(size)
        ev.append(f"Dialogue: 4,{span},Hook,,0,0,0,,{{\\an5\\pos({W // 2},{cy:.0f}){fade}{spring}\\fs{size}}}{line}")
    return ev


def build_ass(words, start, end, hook, style_name, highlight, hook_y, cap_y, edit_style="pro") -> str:
    st = STYLES.get(style_name, STYLES["bounce"])
    cfg = _PRESET_CFG.get(edit_style)
    words = [dict(w, s=w["s"] - CAPTION_LEAD, e=w["e"] - CAPTION_LEAD) for w in words]
    # Support both single-word highlights and individual words from multi-word highlight phrases.
    strong = set()
    for h in highlight or []:
        if not isinstance(h, str):
            continue
        for tok in h.split():
            k = _key(tok)
            if len(k) >= 2:
                strong.add(k)
    shadow_px = 4 if cfg else 0
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},&H00{st['base']},&H00{st['base']},&H00000000,&H78000000,0,0,0,0,100,100,0,0,1,{st['outline']},{shadow_px},5,0,0,0,1
Style: Hook,{HOOK['font']},{HOOK['size']},&H00{HOOK['ink']},&H00{HOOK['ink']},&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1
Style: Scrim,{HOOK['font']},40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    dur = max(0.05, end - start)
    ev = []
    if cfg and cfg.get("caption_scrim") and words:
        ev.extend(_bottom_scrim_events(dur, cap_y))
    ev.extend(_hook_events(_clean(hook), dur, hook_y, edit_style=edit_style))
    if cfg and cfg.get("progress_bar"):
        ev.extend(_progress_bar_events(dur, style_name))
    f = _Font.get(st["font"])
    inside = [dict(w, w=_clean(w["w"])) for w in words if w["s"] >= start - 0.05 and w["s"] < end]
    inside = sorted((w for w in inside if w["w"]), key=lambda w: w["s"])
    for i, w in enumerate(inside[:-1]):
        w["e"] = max(w["s"], min(w["e"], inside[i + 1]["s"]))
    groups = _groups(inside, st["per"])
    for gi, g in enumerate(groups):
        g_start = max(0.0, g[0]["s"] - start)
        g_end = min(g[-1]["e"] + 0.25, groups[gi + 1][0]["s"] if gi + 1 < len(groups) else end) - start
        if g_end - g_start < 0.02:
            continue
        texts = []
        for x in g:
            t = re.sub(r"[.,;:]+$", "", x["w"])
            texts.append(t.upper() if st["upper"] else t)
        if not all(roman.drawable(t) for t in texts):
            line = " ".join(texts)
            fs = max(44, min(int(st["size"] * 0.7), int(1500 / max(1, len(line)) * 1.6)))
            ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(g_end)},Cap,,60,60,0,,{{\\an5\\pos({W // 2},{cap_y:.0f})\\q0\\fs{fs}}}{line}")
            continue
        size = st["size"]
        gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]
        total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        if total > CAP_MAX_W:
            size = max(36, int(size * CAP_MAX_W / total))
            gap = f.width("n n", size) - 2 * f.width("n", size) + (2 * st["outline"] + 6) * size / st["size"]
            total = sum(f.width(t, size) for t in texts) + gap * (len(texts) - 1)
        cy = cap_y - f.cap_shift(size)
        x = (W - total) / 2
        for i, (w, t) in enumerate(zip(g, texts)):
            wd = f.width(t, size)
            is_hi = _key(w["w"]) in strong
            p_base = st["pop"] + (8 if (is_hi and cfg) else 0)
            p = max(106, min(p_base, 100 + int(2500 / max(wd, 1))))
            pos = f"\\an5\\pos({x + wd / 2:.0f},{cy:.0f})\\fs{size}"
            x += wd + gap
            a = max(g_start, w["s"] - start)
            b = (g[i + 1]["s"] - start) if i + 1 < len(g) else g_end
            base = st["strong"] if is_hi else st["base"]
            active_col = "00E5FF" if (is_hi and cfg and style_name != "hype") else st["active"]
            if a - g_start >= 0.02:
                ev.append(f"Dialogue: 1,{_ts(g_start)},{_ts(a)},Cap,,0,0,0,,{{{pos}\\c&H{base}&}}{t}")
            if b - a >= 0.02:
                ev.append(f"Dialogue: 2,{_ts(a)},{_ts(b)},Cap,,0,0,0,,{{{pos}\\c&H{active_col}&\\fscx92\\fscy92"
                          f"\\t(0,75,\\fscx{p}\\fscy{p})\\t(75,180,\\fscx{p - 4}\\fscy{p - 4})}}{t}")
            if g_end - b >= 0.02:
                ev.append(f"Dialogue: 1,{_ts(b)},{_ts(g_end)},Cap,,0,0,0,,{{{pos}\\c&H{base}&\\fscx{p - 4}\\fscy{p - 4}"
                          f"\\t(0,85,\\fscx100\\fscy100)}}{t}")
    return head + "\n".join(ev) + "\n"


PIP_W = 380            # how wide the facecam is in the 1080-wide frame
PIP_MARGIN = 26
PIP_BORDER = 4
SPLIT_H = H // 2


def _has_facecam(facecam):
    """A usable webcam box has four finite coordinates and non-trivial width and height."""
    try:
        x, y, w, h = (float(v) for v in facecam)
        return (len(facecam) == 4 and all(math.isfinite(v) for v in (x, y, w, h))
                and x >= 0 and y >= 0 and w > 8 and h > 8)
    except (TypeError, ValueError, IndexError):
        return False


def _split_game_crop(src_w, src_h, facecam):
    """Choose the largest 9:8 gameplay crop that avoids the webcam, when one can be found."""
    try:
        src_w, src_h = int(src_w), int(src_h)
    except (TypeError, ValueError):
        return 0, 0, 0, 0
    if src_w < 2 or src_h < 2:
        return 0, 0, 0, 0

    ratio = W / SPLIT_H
    max_h = min(float(src_h), src_w / ratio)
    max_h = max(2, int(max_h))
    try:
        fx, fy, fw, fh = (float(v) for v in facecam)
        if fw <= 8 or fh <= 8:
            raise ValueError("webcam is too small")
        pad_x, pad_y = max(2, int(src_w * 0.02)), max(2, int(src_h * 0.02))
        bx0, by0 = max(0.0, fx - pad_x), max(0.0, fy - pad_y)
        bx1, by1 = min(float(src_w), fx + fw + pad_x), min(float(src_h), fy + fh + pad_y)
    except (TypeError, ValueError):
        bx0 = by0 = bx1 = by1 = 0.0

    # Work down in two-pixel steps. This finds the largest clean crop even when a webcam sits
    # near the middle of the source, without sacrificing more gameplay than necessary.
    first_h = max(2, int(max_h) // 2 * 2)
    for crop_h in range(first_h, 1, -2):
        crop_w = min(src_w, max(2, int(round(crop_h * ratio))))
        x_max, y_max = src_w - crop_w, src_h - crop_h
        xs = {0, x_max, x_max // 2}
        ys = {0, y_max, y_max // 2}
        if bx1 > bx0 and by1 > by0:
            x_left = max(0, min(x_max, int(round(bx0 - crop_w))))
            x_right = max(0, min(x_max, int(round(bx1))))
            y_top = max(0, min(y_max, int(round(by0 - crop_h))))
            y_bottom = max(0, min(y_max, int(round(by1))))
            xs.update((x_left, x_right))
            ys.update((y_top, y_bottom))
        best = None
        for x in xs:
            for y in ys:
                overlaps = (bx1 > bx0 and by1 > by0 and x < bx1 and x + crop_w > bx0
                            and y < by1 and y + crop_h > by0)
                if overlaps:
                    continue
                center_distance = abs(x + crop_w / 2 - src_w / 2) + abs(y + crop_h / 2 - src_h / 2)
                candidate = (center_distance, x, y)
                if best is None or candidate < best:
                    best = candidate
        if best:
            return best[1], best[2], crop_w, crop_h

    crop_h = max(2, int(max_h))
    crop_w = min(src_w, max(2, int(round(crop_h * ratio))))
    return (src_w - crop_w) // 2, (src_h - crop_h) // 2, crop_w, crop_h


def _hook_y(layout, hook_y, facecam=None):
    """Where the heading goes. In the gameplay layout the webcam sits in the top corner, so a heading
    across the top would run under it: it goes just below the picture instead."""
    if layout == "gameplay" and _has_facecam(facecam):
        return PIP_MARGIN + int(round(PIP_W * facecam[3] / facecam[2])) + 2 * PIP_BORDER + 36
    return hook_y


# ---------------------------------------------------------------- signal analysis (audio & visual)
def analyze_audio_segment(src: Path | None, start: float, end: float) -> dict | None:
    """Measure 100ms RMS energy envelope & peaks for a clip window via a fast single ffmpeg decode.

    Returns None when the source file does not exist or has no decodable audio.
    """
    dur = max(0.0, float(end) - float(start))
    if not src or dur < 0.8 or not Path(src).is_file():
        return None
    sr = 8000
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, float(start)):.2f}", "-t", f"{dur:.2f}",
             "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
            capture_output=True, creationflags=NO_WINDOW, timeout=30)
    except Exception:  # noqa: BLE001
        return None
    raw = proc.stdout or b""
    if proc.returncode != 0 or len(raw) < sr * 4 // 2:
        return None
    samples = np.frombuffer(raw[:len(raw) - (len(raw) % 4)], dtype=np.float32)
    hop = sr // 10  # 100ms frames
    n = len(samples) // hop
    if n < 5:
        return None
    frames = samples[:n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-10)
    db = 20.0 * np.log10(rms)
    med = float(np.median(db))
    p20, p90 = float(np.percentile(db, 20)), float(np.percentile(db, 90))
    dyn = p90 - p20
    # Detect genuine acoustic peaks & speech/action onsets (above local baseline + clear rise)
    peaks = []
    for i in range(2, n - 2):
        t = i * 0.1
        if t < 0.85 or t > dur - 0.85:
            continue
        if db[i] >= db[i - 1] and db[i] > db[i + 1]:
            lo_bg = float(np.min(db[max(0, i - 5):i]))
            rise = float(db[i] - lo_bg)
            above_med = float(db[i] - med)
            if db[i] > -34.0 and ((above_med >= 4.0 and rise >= 3.0) or (above_med >= 6.0 and rise >= 2.0)):
                peaks.append({"t": round(t, 2), "db": round(float(db[i]), 1), "prominence": round(above_med + 0.5 * rise, 2)})
    peaks.sort(key=lambda p: -p["prominence"])
    return {
        "has_audio": True,
        "db": db,
        "median_db": round(med, 1),
        "p90_db": round(p90, 1),
        "dynamic_range": round(dyn, 1),
        "quiet_uniform": bool(p90 < -42.0 or (dyn < 2.2 and p90 < -26.0)),
        "peaks": peaks[:16],
    }


def analyze_visual_segment(src: Path | None, start: float, end: float) -> dict | None:
    """Sample low-res grayscale motion diffs at 4fps to spot genuine scene cuts or sudden action bursts."""
    dur = max(0.0, float(end) - float(start))
    if not src or dur < 2.0 or not Path(src).is_file():
        return None
    vw, vh, fps = 128, 72, 4
    try:
        proc = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, float(start)):.2f}", "-t", f"{dur:.2f}",
             "-i", str(src), "-an", "-vf", f"fps={fps},scale={vw}:{vh},format=gray",
             "-f", "rawvideo", "-"],
            capture_output=True, creationflags=NO_WINDOW, timeout=30)
    except Exception:  # noqa: BLE001
        return None
    raw = proc.stdout or b""
    frame_sz = vw * vh
    n = len(raw) // frame_sz
    if n < 6:
        return None
    frames = np.frombuffer(raw[:n * frame_sz], dtype=np.uint8).reshape(n, frame_sz).astype(np.float32)
    diffs = np.mean(np.abs(np.diff(frames, axis=0)), axis=1)
    med = float(np.median(diffs))
    p90 = float(np.percentile(diffs, 90))
    cuts = []
    for i in range(1, len(diffs) - 1):
        t = (i + 1) / fps
        if t < 1.0 or t > dur - 1.0:
            continue
        if diffs[i] >= diffs[i - 1] and diffs[i] > diffs[i + 1]:
            if diffs[i] > max(14.0, med * 2.4):
                cuts.append({"t": round(t, 2), "strength": round(float(diffs[i] / max(med, 1.0)), 2)})
    cuts.sort(key=lambda c: -c["strength"])
    return {
        "median_motion": round(med, 2),
        "p90_motion": round(p90, 2),
        "cuts": cuts[:12],
    }


def _energy_at(audio: dict | None, rel_t: float) -> float:
    """Relative energy prominence (dB above median) around `rel_t`."""
    if not audio or "db" not in audio:
        return 0.0
    db = audio["db"]
    idx = max(0, min(len(db) - 1, int(round(rel_t * 10))))
    lo, hi = max(0, idx - 2), min(len(db), idx + 3)
    return float(np.max(db[lo:hi]) - audio["median_db"])


def _match_highlights(spoken: list[dict], highlights: list) -> list[tuple[float, str]]:
    """Match both single-word AND multi-word highlight phrases against spoken transcript tokens."""
    if not spoken or not highlights:
        return []
    keys = [_key(w["w"]) for w in spoken]
    hits = []
    for raw in highlights:
        if not isinstance(raw, str):
            continue
        toks = [_key(tok) for tok in raw.split() if _key(tok)]
        if not toks:
            continue
        if len(toks) == 1:
            target = toks[0]
            for i, k in enumerate(keys):
                if k == target:
                    hits.append((spoken[i]["s"], spoken[i]["w"]))
        else:
            m = len(toks)
            for i in range(len(keys) - m + 1):
                if keys[i:i + m] == toks:
                    hits.append((spoken[i]["s"], " ".join(w["w"] for w in spoken[i:i + m])))
    return hits


def _transcript_pacing_candidates(spoken: list[dict], start: float, dur: float, audio: dict | None) -> list[dict]:
    """Non-AI transcript turning points: post-pause re-entries, exclamations/questions, and cadence shifts."""
    if len(spoken) < 4:
        return []
    out = []
    for i, w in enumerate(spoken):
        rel = w["s"] - start
        if rel < 1.0 or rel > dur - 1.1:
            continue
        prev = spoken[i - 1] if i > 0 else None
        gap = (w["s"] - prev["e"]) if prev else 0.0
        txt = w["w"].strip()
        energy = _energy_at(audio, rel)
        # 1. Strong pause followed by a fresh beat (classic setup -> payoff pivot)
        if gap >= 0.48:
            score = 2.4 + min(1.5, gap) + max(0.0, energy * 0.25)
            out.append({"t": rel, "score": score, "source": "transcript", "word": txt,
                        "reason": f"Pause-to-entry shift on \"{txt}\""})
        # 2. Exclamation or question punchline
        if prev and prev["w"].strip()[-1:] in "!?":
            score = 2.2 + max(0.0, energy * 0.3)
            out.append({"t": max(1.0, prev["s"] - start), "score": score, "source": "transcript",
                        "word": prev["w"].strip(), "reason": f"Emphatic line \"{prev['w'].strip()}\""})
        elif txt[-1:] in "!?":
            score = 2.3 + max(0.0, energy * 0.3)
            out.append({"t": rel, "score": score, "source": "transcript", "word": txt,
                        "reason": f"Emphatic line \"{txt}\""})
    # 3. For longer speech-heavy clips with steady delivery (no long pauses or exclamation marks),
    # find genuine clause starts near natural narrative pivots (~32% and ~68% of clip) — never arbitrary dead air.
    if not out and dur >= 8.0 and len(spoken) >= 12:
        pivots = [0.34 * dur, 0.68 * dur] if dur >= 14.0 else [0.46 * dur]
        # Prefer sentence/clause boundaries first
        clauses = []
        for i, w in enumerate(spoken):
            rel = w["s"] - start
            if rel < 1.4 or rel > dur - 1.4:
                continue
            prev = spoken[i - 1] if i > 0 else None
            if prev and (prev["w"].strip()[-1:] in ".?!" or (w["s"] - prev["e"]) >= 0.25):
                clauses.append(w)
        pool = clauses if clauses else [w for w in spoken if 1.4 <= w["s"] - start <= dur - 1.4]
        for target in pivots:
            if not pool:
                break
            best = min(pool, key=lambda w: abs((w["s"] - start) - target) - max(0.0, _energy_at(audio, w["s"] - start)) * 0.25)
            rel = best["s"] - start
            out.append({"t": rel, "score": 1.6 + max(0.0, _energy_at(audio, rel) * 0.2),
                        "source": "transcript", "word": best["w"].strip(),
                        "reason": f"Spoken clause pivot on \"{best['w'].strip()}\""})
    out.sort(key=lambda c: -c["score"])
    return out


def _fmt_time(sec: float) -> str:
    s = max(0.0, float(sec))
    m, r = divmod(s, 60.0)
    return f"{int(m)}:{r:04.1f}" if m >= 1 else f"{r:.1f}s"


def detect_jump_cuts(audio: dict | None, words: list, start: float, dur: float, cfg: dict) -> dict:
    """Detect awkward dead-air silence pauses and build tightened keep-segments for jump-cut pacing.

    Never cuts inside spoken words (keeps a 0.15s breath margin around every word).
    Returns {"segments": [(s, e), ...], "cuts": [new_t, ...], "trimmed_sec": float, "effective_dur": float}.
    """
    identity = {"segments": [(0.0, round(dur, 3))], "cuts": [], "trimmed_sec": 0.0, "effective_dur": round(dur, 3)}
    if not cfg.get("jump_cuts") or dur < 6.5 or not audio or audio.get("quiet_uniform"):
        return identity
    db = audio.get("db")
    if db is None or len(db) < 20:
        return identity

    min_sil = float(cfg.get("cut_min_silence", 0.72))
    keep_pause = float(cfg.get("cut_keep_pause", 0.16))
    med = float(audio.get("median_db", -24.0))
    thresh = min(-32.0, max(-46.0, med - 11.5))

    # Build occupied speech intervals (clip-relative) with breath safety margin so no syllable is ever clipped.
    pad = 0.15
    speech_spans = []
    for w in words or []:
        ws = float(w.get("s", 0.0)) - start
        we = float(w.get("e", ws + 0.12)) - start
        if we > 0.0 and ws < dur:
            speech_spans.append((max(0.0, ws - pad), min(dur, we + pad)))
    speech_spans.sort()
    merged_speech = []
    for s0, e0 in speech_spans:
        if merged_speech and s0 <= merged_speech[-1][1]:
            merged_speech[-1] = (merged_speech[-1][0], max(merged_speech[-1][1], e0))
        else:
            merged_speech.append((s0, e0))

    def _in_speech(t0: float, t1: float) -> bool:
        for ss, se in merged_speech:
            if ss < t1 and se > t0:
                return True
        return False

    # Find quiet runs in 100ms audio frames strictly inside [0.6, dur - 0.6]
    quiet_runs = []
    run_start = None
    n = len(db)
    for i in range(n):
        t = i * 0.1
        is_q = (0.6 <= t <= dur - 0.6) and (float(db[i]) < thresh) and not _in_speech(t, t + 0.1)
        if is_q and run_start is None:
            run_start = t
        elif not is_q and run_start is not None:
            if t - run_start >= min_sil:
                quiet_runs.append((run_start, t))
            run_start = None
    if run_start is not None:
        end_t = min(dur - 0.6, n * 0.1)
        if end_t - run_start >= min_sil:
            quiet_runs.append((run_start, end_t))

    if not quiet_runs:
        return identity

    # Keep at most 6 longest dead-air cuts, ensuring segments stay >= 0.9s long
    quiet_runs.sort(key=lambda r: -(r[1] - r[0]))
    chosen = sorted(quiet_runs[:6], key=lambda r: r[0])

    # Convert dead-air cuts into keep segments, leaving `keep_pause` (split across edges) for natural rhythm.
    half = keep_pause / 2.0
    remove_spans = []
    for rs, re_t in chosen:
        a = round(rs + half, 2)
        b = round(re_t - half, 2)
        if b - a >= 0.30:
            remove_spans.append((a, b))

    if not remove_spans:
        return identity

    segments = []
    cursor = 0.0
    for a, b in remove_spans:
        if a - cursor >= 0.85:
            segments.append((round(cursor, 3), round(a, 3)))
            cursor = b
    if dur - cursor >= 0.85:
        segments.append((round(cursor, 3), round(dur, 3)))
    elif segments:
        segments[-1] = (segments[-1][0], round(dur, 3))
    else:
        return identity

    if len(segments) <= 1:
        return identity

    effective = round(sum(e - s for s, e in segments), 3)
    trimmed = round(dur - effective, 2)
    if trimmed < 0.45 or effective < 3.5:
        return identity

    cuts = []
    acc = 0.0
    for s, e in segments[:-1]:
        acc += (e - s)
        cuts.append(round(acc, 3))
    return {"segments": segments, "cuts": cuts, "trimmed_sec": trimmed, "effective_dur": effective}


def remap_time(rel_t: float, segments: list[tuple[float, float]]) -> float | None:
    """Map an original clip-relative timestamp into the jump-cut tightened timeline (None if inside cut silence)."""
    acc = 0.0
    for s, e in segments:
        if rel_t < s:
            return round(acc, 3)
        if s <= rel_t <= e:
            return round(acc + (rel_t - s), 3)
        acc += (e - s)
    return round(acc, 3)


def remap_words(words: list, start: float, segments: list[tuple[float, float]]) -> list:
    """Shift transcript words so caption timestamps stay frame-locked after dead-air jump-cuts."""
    if len(segments) <= 1:
        return words
    out = []
    for w in words or []:
        ws = float(w["s"]) - start
        we = float(w["e"]) - start
        if we <= 0.0 or ws >= segments[-1][1] + 0.1:
            continue
        ns = remap_time(max(0.0, ws), segments)
        ne = remap_time(max(ws + 0.04, we), segments)
        if ns is not None and ne is not None:
            ne = max(ns + 0.05, ne)
            out.append(dict(w, s=round(start + ns, 3), e=round(start + ne, 3)))
    return out


def plan_edits(clip: dict, words: list, src: Path | None = None, layout: str = "blur",
               facecam=None, audio_info: dict | None = None, visual_info: dict | None = None) -> dict:
    """Build a complete, transparent, signal-aware edit plan for one clip.

    Combines AI beats, single/multi-word highlights, transcript pacing, audio loudness/onset energy,
    and visual scene/motion cues — while strictly refusing to force arbitrary edits onto unsuitable
    (off, too-short, or uniformly quiet) clips.
    """
    style = normalize_edit_style(clip)
    sound_req = bool(clip.get("sound_fx", True))
    start, end = float(clip.get("start", 0.0)), float(clip.get("end", 0.0))
    dur = max(0.0, end - start)
    facecam_safe = bool(layout in ("gameplay", "split") and _has_facecam(facecam))

    def _empty(skipped_reason: str, signals: list[str] | None = None) -> dict:
        sigs = signals or []
        return {
            "style": style,
            "sound_fx": sound_req,
            "sound_active": False,
            "applied": False,
            "studio_voice": False,
            "progress_bar": False,
            "dolly_drift": 0.0,
            "effective_dur": round(dur, 3),
            "jump_cuts": {"segments": [(0.0, round(dur, 3))], "cuts": [], "trimmed_sec": 0.0, "angle_segments": []},
            "opening_intro": None,
            "color_grade": None,
            "beats": [],
            "signals_used": sigs,
            "facecam_safe": facecam_safe,
            "summary": {
                "style": style,
                "sound_fx": sound_req,
                "applied": False,
                "reason": skipped_reason,
                "skipped_reason": skipped_reason,
                "signals_used": sigs,
                "treatments": [],
                "facecam_safe": facecam_safe,
                "beat_count": 0,
                "sound_count": 0,
                "jump_cut_count": 0,
                "trimmed_sec": 0.0,
                "studio_voice": False,
                "progress_bar": False,
            },
        }

    if style == "off":
        return _empty("Editing is set to Off — video and audio left unedited.")

    if dur < 3.0:
        return _empty("Clip is under 3 seconds — left unedited to keep playback steady.")

    cfg = _PRESET_CFG[style]
    spoken = sorted((w for w in (words or []) if start + 0.6 <= w["s"] <= end - 0.6), key=lambda w: w["s"])
    all_clip_words = sorted((w for w in (words or []) if start - 0.1 <= w["s"] <= end + 0.1), key=lambda w: w["s"])

    audio = audio_info if audio_info is not None else analyze_audio_segment(src, start, end)
    visual = visual_info if visual_info is not None else analyze_visual_segment(src, start, end)

    signals_used = []
    candidates = []

    # 1. AI edit_beats (accept BOTH absolute [start, end] and clip-relative [0, dur], deduplicated)
    for raw in (clip.get("edit_beats") or [])[:10]:
        val = raw.get("at") if isinstance(raw, dict) else raw
        try:
            t = float(val)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(t):
            continue
        if start <= t <= end:
            if not (start + 0.65 <= t <= end - 0.65):
                continue
            rel = t - start
        elif start >= 1.0 and 0.65 <= t <= dur - 0.65:
            rel = t
        else:
            continue
        # Snap cleanly to nearest spoken word (within 0.75s) OR nearby audio peak (within 0.65s)
        snapped = None
        word_label = ""
        if spoken:
            nearest = min(spoken, key=lambda w: abs((w["s"] - start) - rel))
            if abs((nearest["s"] - start) - rel) <= 0.75:
                snapped = nearest["s"] - start
                word_label = nearest["w"].strip()
        if snapped is None and audio and audio.get("peaks"):
            nearest_p = min(audio["peaks"], key=lambda p: abs(p["t"] - rel))
            if abs(nearest_p["t"] - rel) <= 0.65:
                snapped = nearest_p["t"]
        if snapped is None:
            # If the clip has NO transcript at all (e.g. captions-off without words.json), only trust
            # AI beats if backed by audio or visual cues, never hallucinated timestamps.
            if not spoken and not (audio and audio.get("peaks")) and not (visual and visual.get("cuts")):
                continue
            if spoken:
                # Hallucinated timestamp far from any spoken word and no audio peak: skip
                continue
            snapped = rel
        if 0.65 <= snapped <= dur - 0.65:
            score = 5.0 + max(0.0, _energy_at(audio, snapped) * 0.2)
            candidates.append({
                "t": round(snapped, 2),
                "score": score,
                "source": "ai_beat",
                "word": word_label,
                "reason": f"AI turning point on \"{word_label}\"" if word_label else "AI turning point aligned with audio peak",
            })
            if "ai_beats" not in signals_used:
                signals_used.append("ai_beats")

    # 2. Highlight words (single AND multi-word phrases)
    hl_hits = _match_highlights(spoken, clip.get("highlight_words") or [])
    for abs_t, phrase in hl_hits:
        rel = round(abs_t - start, 2)
        if 0.65 <= rel <= dur - 0.65:
            candidates.append({
                "t": rel,
                "score": 4.2 + max(0.0, _energy_at(audio, rel) * 0.25),
                "source": "highlight",
                "word": phrase,
                "reason": f"Highlighted moment \"{phrase}\"",
            })
            if "transcript" not in signals_used:
                signals_used.append("transcript")

    # 3. Audio loudness / onset peaks (works even when captions are off or AI beats are empty!)
    if audio and not audio.get("quiet_uniform") and audio.get("peaks"):
        for pk in audio["peaks"]:
            t = pk["t"]
            # If spoken words exist, align peak gently to a nearby word onset for crisp editorial timing
            word_label = ""
            if spoken:
                nearest = min(spoken, key=lambda w: abs((w["s"] - start) - t))
                if abs((nearest["s"] - start) - t) <= 0.45:
                    t = round(nearest["s"] - start, 2)
                    word_label = nearest["w"].strip()
            candidates.append({
                "t": t,
                "score": 3.2 + min(2.0, pk["prominence"] * 0.18),
                "source": "audio_energy",
                "word": word_label,
                "reason": f"Audio energy peak (+{pk['db'] - audio['median_db']:.0f} dB)" + (f" on \"{word_label}\"" if word_label else ""),
            })
        if "audio_energy" not in signals_used:
            signals_used.append("audio_energy")

    # 4. Non-AI transcript pacing fallback (pauses, exclamations, clause pivots)
    t_cands = _transcript_pacing_candidates(spoken, start, dur, audio)
    if t_cands:
        candidates.extend(t_cands)
        if "transcript" not in signals_used:
            signals_used.append("transcript")

    # 5. Visual scene / action cuts (especially helpful for gameplay clips with sparse speech)
    if visual and visual.get("cuts"):
        for cut in visual["cuts"]:
            candidates.append({
                "t": cut["t"],
                "score": 2.6 + min(1.5, cut["strength"] * 0.35),
                "source": "visual_scene",
                "word": "",
                "reason": "Visual scene/action shift",
            })
        if "visual_scene" not in signals_used:
            signals_used.append("visual_scene")

    # Restraint check: if there are NO genuine signals at all (e.g., quiet/uniform silent clip with no words & no motion)
    has_speech = len(all_clip_words) >= 3
    audio_quiet_flat = bool(audio and audio.get("quiet_uniform"))
    has_visual_action = bool(visual and visual.get("cuts"))
    if not candidates and (audio_quiet_flat or (not has_speech and not has_visual_action)):
        why = ("Clip is uniformly quiet with no speech or motion peaks — left unedited to avoid forcing arbitrary effects."
               if audio_quiet_flat else
               "No speech, audio peaks, or visual cuts detected — left unedited to avoid forcing arbitrary effects.")
        return _empty(why, signals=signals_used)

    # Select best spaced beats up to preset cap (scale gracefully with clip duration)
    if dur < 5.0:
        max_beats = 0 if not candidates else 1
    elif dur < 14.0:
        max_beats = min(2 if style == "balanced" else 3, cfg["max_beats"])
    elif dur >= 40.0:
        max_beats = cfg["max_beats_long"]
    else:
        max_beats = cfg["max_beats"]

    # Sort by priority score (higher first), pick spaced beats, then sort chronologically
    min_gap = cfg["min_gap"]
    candidates.sort(key=lambda c: (-c["score"], c["t"]))
    picked_raw = []
    for cand in candidates:
        t = cand["t"]
        # Ensure beat + hold stays cleanly inside the clip
        if t < 0.85 or t > dur - 1.1:
            continue
        if all(abs(t - p["t"]) >= min_gap for p in picked_raw):
            picked_raw.append(cand)
        if len(picked_raw) >= max_beats:
            break
    picked_raw.sort(key=lambda c: c["t"])

    # Determine if this is a calm talking clip (speech present, low audio variance) -> gentler zoom
    is_calm_speech = has_speech and bool(audio and audio.get("dynamic_range", 9.0) < 5.0)

    # Dead-air silence jump-cut tightening (Pro & Energetic): removes awkward dead pauses while keeping breath margins
    jc = detect_jump_cuts(audio, all_clip_words, start, dur, cfg)
    segments = jc["segments"]
    effective_dur = float(jc["effective_dur"])
    cut_points = list(jc["cuts"])
    trimmed_sec = float(jc["trimmed_sec"])

    # Assign narrative roles (setup -> payoff -> reaction) based on energy & timeline position (remapped to tightened timeline)
    beats = []
    if picked_raw:
        # Payoff = highest combined score / energy beat (prefer middle-to-late clip if tied)
        payoff_idx = max(range(len(picked_raw)), key=lambda i: (
            picked_raw[i]["score"] + _energy_at(audio, picked_raw[i]["t"]) * 0.25
            + (0.35 if 0.30 * dur <= picked_raw[i]["t"] <= 0.85 * dur else 0.0)
        ))
        pan_dirs = [-1.0, 1.0, -0.7, 0.8, -0.5, 0.6]
        for i, item in enumerate(picked_raw):
            remapped_t = remap_time(item["t"], segments) if len(segments) > 1 else item["t"]
            if remapped_t is None or remapped_t < 0.65 or remapped_t > effective_dur - 0.85:
                continue
            if i == payoff_idx:
                role = "payoff"
            elif i < payoff_idx:
                role = "setup"
            else:
                role = "reaction"

            if is_calm_speech:
                zoom = cfg["zoom_calm"] if role != "payoff" else min(cfg["zoom_payoff"], cfg["zoom_calm"] + 0.05)
            else:
                zoom = cfg[f"zoom_{role}"]

            # Slightly restrain gameplay background zoom so gameplay action stays legible
            if layout in ("gameplay", "split"):
                zoom = round(1.0 + (zoom - 1.0) * 0.82, 3)

            hold = min(cfg[f"hold_{role}"], max(0.45, (effective_dur - remapped_t) - cfg["ease_in"] - cfg["ease_out"] - 0.12))

            # Subtle reframing pan (alternating horizontal + subtle upward focus toward faces/action)
            pan_x = round(pan_dirs[i % len(pan_dirs)] * cfg["pan_scale"] * (0.0 if role == "payoff" else 1.0), 3)
            pan_y = round(-0.20 * cfg["pan_scale"] if role in ("payoff", "reaction") else 0.0, 3)

            # Sound accent selection: layered cinematic SFX aligned to narrative role
            if not sound_req:
                snd = None
            elif role == "payoff":
                snd = "impact" if style in ("pro", "energetic") else "accent"
            elif role == "reaction":
                snd = "pop"
            else:
                snd = "whoosh" if (style in ("pro", "energetic") or item["source"] in ("ai_beat", "audio_energy")) else None

            beats.append({
                "at": round(remapped_t, 2),
                "role": role,
                "zoom": round(zoom, 3),
                "hold": round(hold, 2),
                "ease_in": cfg["ease_in"],
                "ease_out": cfg["ease_out"],
                "pan_x": pan_x,
                "pan_y": pan_y,
                "impact_shake": round(cfg["impact_shake"] * (0.6 if layout in ("gameplay", "split") else 1.0), 2) if role == "payoff" else 0.0,
                "impact_flash": round(cfg["impact_flash"], 3) if role == "payoff" else 0.0,
                "sound": snd,
                "source": item["source"],
                "word": item.get("word", ""),
                "reason": item["reason"],
            })

        # Ensure beats never overlap each other's ease-out / ease-in after remapping
        for i in range(len(beats) - 1):
            avail = beats[i + 1]["at"] - beats[i]["at"] - beats[i]["ease_in"] - beats[i]["ease_out"] - 0.18
            beats[i]["hold"] = round(max(0.35, min(beats[i]["hold"], avail)), 2)

    # Build alternating multi-cam angle windows on jump-cut segments (odd segments get a crisp studio medium punch-in)
    angle_segments = []
    if len(segments) > 1 and cfg.get("cut_alt_zoom", 0.0) > 0.0:
        alt_z = cfg["cut_alt_zoom"] * (0.65 if layout in ("gameplay", "split") else 1.0)
        cursor = 0.0
        for idx_seg, (s0, e0) in enumerate(segments):
            seg_len = round(e0 - s0, 3)
            if idx_seg % 2 == 1 and seg_len >= 0.6:
                angle_segments.append({
                    "start": round(cursor, 3),
                    "end": round(cursor + seg_len, 3),
                    "zoom_delta": round(alt_z, 3),
                })
            cursor += seg_len

    # Opening arrival treatment (smooth settle from push-in into framing on clip start)
    opening_intro = None
    if effective_dur >= 4.0 and (has_speech or beats or (audio and not audio_quiet_flat)):
        intro_dur = cfg["intro_dur"]
        if beats:
            intro_dur = min(intro_dur, max(0.55, beats[0]["at"] - 0.12))
        if intro_dur >= 0.55:
            opening_intro = {
                "duration": round(intro_dur, 2),
                "zoom_start": cfg["intro_zoom"],
                "zoom_end": 1.0,
                "sound": bool(sound_req and cfg["intro_sound"] and (not beats or beats[0]["at"] > 1.4)),
                "reason": "Snappy opening arrival ease to hook the viewer on frame one",
            }

    # Continuous subtle living dolly creep so no shot ever feels dead static
    dolly_drift = round(cfg.get("dolly_drift", 0.0) * (0.65 if layout in ("gameplay", "split") else 1.0), 4) if effective_dur >= 4.0 else 0.0

    # Color & clarity treatment (film S-curve + split tone + clarity sharpen + vignette + climax flash pop)
    payoff_beat = next((b for b in beats if b["role"] == "payoff"), None)
    grade_labels = {
        "balanced": "Film clarity & subtle split-tone grade",
        "pro": "Pro film S-curve, split-tone & clarity grade",
        "energetic": "Punchy cinema S-curve, split-tone & climax flash grade",
    }
    color_grade = {
        "contrast": cfg["contrast"],
        "saturation": cfg["saturation"],
        "brightness": cfg["brightness"],
        "sharpen": cfg["sharpen"],
        "vignette": cfg["vignette"],
        "s_curve": bool(cfg.get("s_curve")),
        "split_tone": bool(cfg.get("split_tone")),
        "flash_at": payoff_beat["at"] if (payoff_beat and payoff_beat.get("impact_flash", 0) > 0) else None,
        "flash_amp": payoff_beat["impact_flash"] if (payoff_beat and payoff_beat.get("impact_flash", 0) > 0) else 0.0,
        "label": grade_labels.get(style, "Pro film grade"),
    }

    studio_voice = bool(cfg.get("studio_voice"))
    progress_bar = bool(cfg.get("progress_bar"))

    # Build human-readable summary & treatments list
    treatments = []
    if len(segments) > 1:
        treatments.append({
            "kind": "pacing",
            "at": 0.0,
            "label": f"Jump-cut pacing ({len(cut_points)} cuts, -{trimmed_sec:.1f}s dead air)",
            "detail": f"Tightened {len(cut_points)} silent pause{'s' if len(cut_points) != 1 else ''} with alternating multi-cam studio framing & locked captions",
            "sound": False,
        })
    if opening_intro:
        treatments.append({
            "kind": "opening",
            "at": 0.0,
            "label": "Opening arrival",
            "detail": f"Snappy {opening_intro['zoom_start']:.2f}x -> 1.00x hook settle over {opening_intro['duration']:.1f}s"
                      + (" with airy whoosh" if opening_intro["sound"] else ""),
            "sound": opening_intro["sound"],
        })
    treatments.append({
        "kind": "grade",
        "label": color_grade["label"],
        "detail": f"Film S-curve, warm/cool split-tone, {color_grade['contrast']:.2f}x contrast, clarity sharpen & focus vignette",
        "sound": False,
    })
    if studio_voice:
        treatments.append({
            "kind": "audio",
            "label": "Studio voice mastering",
            "detail": "75Hz rumble filter, vocal warmth/presence EQ, 3:1 dialogue compression, -14 LUFS loudness & limiter",
            "sound": False,
        })
    role_labels = {"setup": "Setup punch-in", "payoff": "Climax payoff hit", "reaction": "Reaction punch-in"}
    for b in beats:
        extras = []
        if b.get("impact_shake", 0) > 0:
            extras.append("impact micro-shake")
        if b.get("impact_flash", 0) > 0:
            extras.append("luminance pop")
        if b["sound"]:
            extras.append(f"{b['sound']} SFX")
        extra_note = (" + " + ", ".join(extras)) if extras else ""
        treatments.append({
            "kind": "beat",
            "at": b["at"],
            "role": b["role"],
            "source": b["source"],
            "label": f"{role_labels.get(b['role'], 'Emphasis punch-in')} at {_fmt_time(b['at'])}",
            "detail": f"{b['zoom']:.2f}x snappy punch-in ({b['hold']:.1f}s hold){extra_note} — {b['reason']}",
            "sound": bool(b["sound"]),
        })
    if progress_bar:
        treatments.append({
            "kind": "graphics",
            "label": "Pro motion overlays",
            "detail": "Spring hook card, feathered bottom caption scrim, callout highlight pops & animated retention bar",
            "sound": False,
        })
    if facecam_safe:
        treatments.append({
            "kind": "framing",
            "label": "Streamer framing protected" if layout == "split" else "Facecam protected",
            "detail": ("Streamer fills the upper half; motion & impact shake stay on the lower gameplay half."
                       if layout == "split" else
                       "Motion & impact shake applied to gameplay layer only so top-right facecam stays sharp and uncropped"),
            "sound": False,
        })

    sound_count = sum(1 for b in beats if b["sound"]) + (1 if opening_intro and opening_intro["sound"] else 0)
    sound_active = sound_count > 0

    sig_names = {
        "ai_beats": "AI beats",
        "transcript": "transcript pacing",
        "audio_energy": "audio energy",
        "visual_scene": "visual motion",
    }
    sig_text = ", ".join(sig_names[s] for s in signals_used if s in sig_names) or "opening structure"
    parts_summary = []
    if len(segments) > 1:
        parts_summary.append(f"{len(cut_points)} jump-cut{'s' if len(cut_points) != 1 else ''} (-{trimmed_sec:.1f}s dead air)")
    if opening_intro:
        parts_summary.append("opening hook arrival")
    parts_summary.append("film grade & studio voice")
    if beats:
        parts_summary.append(f"{len(beats)} pro punch-in{'s' if len(beats) != 1 else ''}")
    if payoff_beat and payoff_beat.get("impact_shake", 0) > 0:
        parts_summary.append("climax impact hit")
    if sound_active:
        parts_summary.append(f"{sound_count} cinematic SFX")
    elif not sound_req:
        parts_summary.append("SFX muted")
    reason_line = f"{EDIT_STYLE_META[style]['label']} edit: " + ", ".join(parts_summary) + f" (from {sig_text})."
    if facecam_safe:
        reason_line += " Facecam framing protected."

    return {
        "style": style,
        "sound_fx": sound_req,
        "sound_active": sound_active,
        "applied": True,
        "studio_voice": studio_voice,
        "progress_bar": progress_bar,
        "dolly_drift": dolly_drift,
        "effective_dur": effective_dur,
        "jump_cuts": {
            "segments": segments,
            "cuts": cut_points,
            "trimmed_sec": trimmed_sec,
            "angle_segments": angle_segments,
        },
        "opening_intro": opening_intro,
        "color_grade": color_grade,
        "beats": beats,
        "signals_used": signals_used,
        "facecam_safe": facecam_safe,
        "summary": {
            "style": style,
            "sound_fx": sound_req,
            "applied": True,
            "reason": reason_line,
            "skipped_reason": "",
            "signals_used": signals_used,
            "treatments": treatments,
            "facecam_safe": facecam_safe,
            "beat_count": len(beats),
            "sound_count": sound_count,
            "jump_cut_count": len(cut_points),
            "trimmed_sec": trimmed_sec,
            "studio_voice": studio_voice,
            "progress_bar": progress_bar,
        },
    }


def edit_points(clip: dict, words: list, src: Path | None = None, audio_info: dict | None = None,
                visual_info: dict | None = None) -> list[float]:
    """Return clip-relative timestamps of planned editorial beats (compatible helper over `plan_edits`)."""
    plan = plan_edits(clip, words, src=src, layout=clip.get("layout", "blur"), facecam=clip.get("facecam"),
                      audio_info=audio_info, visual_info=visual_info)
    return [b["at"] for b in plan["beats"]]


# ---------------------------------------------------------------- FFmpeg filter builders
def _motion_expr(plan: dict, out_w: int, out_h: int) -> str | None:
    """Build pro camera motion: living dolly creep, opening snap, multi-cam jump-cut framing, punch-ins, and damped climax micro-shake."""
    intro = plan.get("opening_intro")
    beats = plan.get("beats") or []
    dolly = float(plan.get("dolly_drift") or 0.0)
    eff_dur = max(1.0, float(plan.get("effective_dur") or 12.0))
    angle_segs = (plan.get("jump_cuts") or {}).get("angle_segments") or []
    if not intro and not beats and dolly <= 0.0 and not angle_segs:
        return None
    zoom_terms = []
    px_terms = []
    py_terms = []
    shake_x_terms = []
    shake_y_terms = []

    if dolly > 0.001:
        # Continuous subtle living dolly creep across clip (never static/dead)
        zoom_terms.append(f"{dolly:.4f}*min(1,t/{eff_dur:.2f})")

    if intro:
        dz = intro["zoom_start"] - 1.0
        d = max(0.2, float(intro["duration"]))
        zoom_terms.append(f"{dz:.4f}*0.5*(1+cos(PI*t/{d:.3f}))*lt(t,{d:.3f})")

    # Alternating multi-cam studio framing on odd jump-cut segments
    for seg in angle_segs:
        s0, e0, dz = float(seg["start"]), float(seg["end"]), float(seg["zoom_delta"])
        if dz > 0.01 and e0 > s0 + 0.2:
            zoom_terms.append(f"{dz:.4f}*between(t,{s0:.3f},{e0:.3f})")

    for b in beats:
        t0 = float(b["at"])
        ei = max(0.07, float(b["ease_in"]))
        hd = max(0.2, float(b["hold"]))
        eo = max(0.08, float(b["ease_out"]))
        t1 = t0 + ei
        t2 = t1 + hd
        t3 = t2 + eo
        dz = max(0.01, float(b["zoom"]) - 1.0)
        env = (f"(between(t,{t0:.3f},{t1:.3f})*0.5*(1-cos(PI*(t-{t0:.3f})/{ei:.3f}))"
               f"+between(t,{t1:.3f},{t2:.3f})"
               f"+between(t,{t2:.3f},{t3:.3f})*0.5*(1+cos(PI*(t-{t2:.3f})/{eo:.3f})))")
        zoom_terms.append(f"{dz:.4f}*{env}")
        if abs(b.get("pan_x", 0.0)) > 0.001:
            px_terms.append(f"({float(b['pan_x']):.3f})*{env}")
        if abs(b.get("pan_y", 0.0)) > 0.001:
            py_terms.append(f"({float(b['pan_y']):.3f})*{env}")
        amp = float(b.get("impact_shake") or 0.0)
        if amp >= 1.0:
            # Crisp 190ms damped impact micro-shake right on climax beat onset
            ts_end = t0 + 0.19
            shake_x_terms.append(f"{amp:.2f}*sin(2*PI*24*(t-{t0:.3f}))*exp(-15*(t-{t0:.3f}))*between(t,{t0:.3f},{ts_end:.3f})")
            shake_y_terms.append(f"{amp * 0.72:.2f}*cos(2*PI*19*(t-{t0:.3f}))*exp(-15*(t-{t0:.3f}))*between(t,{t0:.3f},{ts_end:.3f})")

    # Cap total zoom at 1.46x so image sharpness stays crisp and never over-crops
    zoom_expr = f"min(1.46,1+{'+'.join(zoom_terms)})"
    base_x = f"(in_w-{out_w})/2"
    if px_terms:
        base_x = f"(in_w-{out_w})/2*(1+({'+'.join(px_terms)}))"
    if shake_x_terms:
        base_x = f"({base_x})+({'+'.join(shake_x_terms)})"
    x_expr = f"max(0,min(in_w-{out_w},{base_x}))" if (px_terms or shake_x_terms) else base_x

    base_y = f"(in_h-{out_h})/2"
    if py_terms:
        base_y = f"(in_h-{out_h})/2*(1+({'+'.join(py_terms)}))"
    if shake_y_terms:
        base_y = f"({base_y})+({'+'.join(shake_y_terms)})"
    y_expr = f"max(0,min(in_h-{out_h},{base_y}))" if (py_terms or shake_y_terms) else base_y

    return (f"scale=w='trunc({out_w}*({zoom_expr})/2)*2':h='trunc({out_h}*({zoom_expr})/2)*2':eval=frame,"
            f"crop={out_w}:{out_h}:x='{x_expr}':y='{y_expr}'")


def _grade_chain(plan: dict) -> str:
    """Fast native-YUV Pro film S-curve, warm/cool color tint, dynamic climax micro-flash, clarity sharpen & vignette."""
    g = plan.get("color_grade")
    if not g:
        return ""
    parts = []
    if g.get("s_curve"):
        # True mathematical film S-curve directly in YUV luma (deepens shadows, lifts highlights, zero RGB slowdown!)
        parts.append("lutyuv=y='clip(val-6*sin(2*PI*(val-16)/219),16,235)'")
    tone = ":gamma_r=1.025:gamma_g=1.004:gamma_b=0.978" if g.get("split_tone") else ""
    flash_at = g.get("flash_at")
    flash_amp = float(g.get("flash_amp") or 0.0)
    if flash_at is not None and flash_amp > 0.005:
        t0 = float(flash_at)
        t1 = t0 + 0.13
        br_expr = f"{g['brightness']:.3f}+{flash_amp:.3f}*exp(-22*(t-{t0:.3f}))*between(t,{t0:.3f},{t1:.3f})"
        parts.append(f"eq=contrast={g['contrast']:.3f}:saturation={g['saturation']:.3f}:brightness='{br_expr}'{tone}:eval=frame")
    else:
        parts.append(f"eq=contrast={g['contrast']:.3f}:saturation={g['saturation']:.3f}:brightness={g['brightness']:.3f}{tone}")
    parts.append(f"unsharp=3:3:{g['sharpen']:.2f}:3:3:0.0")
    parts.append(f"vignette={g['vignette']}")
    return "," + ",".join(parts)


def _jump_cut_video_prefix(plan: dict) -> tuple[str, str]:
    """Build frame-exact jump-cut trim+concat prefix if plan removes dead-air pauses; returns (prefix_graph, src_pad)."""
    segs = (plan.get("jump_cuts") or {}).get("segments") or []
    if len(segs) <= 1:
        return f"[0:v]fps={config.OUT_FPS},setpts=PTS-STARTPTS[vin];", "[vin]"
    parts = [f"[0:v]fps={config.OUT_FPS},setpts=PTS-STARTPTS,split={len(segs)}" + "".join(f"[vs{i}]" for i in range(len(segs))) + ";"]
    for i, (s0, e0) in enumerate(segs):
        parts.append(f"[vs{i}]trim=start={s0:.3f}:end={e0:.3f},setpts=PTS-STARTPTS[vt{i}];")
    parts.append("".join(f"[vt{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=1:a=0[vin];")
    return "".join(parts), "[vin]"


def _filter(layout, fg_h, facecam=None, plan=None, src_w=None, src_h=None):
    """Build the 9:16 layout + jump-cut pacing + facecam-safe pro camera motion + film grade filter graph outputting [v1]."""
    plan = plan or {}
    applied = bool(plan.get("applied"))
    grade = _grade_chain(plan) if applied else ""
    prefix, vpad = _jump_cut_video_prefix(plan) if applied else (f"[0:v]fps={config.OUT_FPS},setpts=PTS-STARTPTS[vin];", "[vin]")

    if layout == "split" and _has_facecam(facecam):
        # A clean top-half streamer panel and lower-half gameplay panel; punch-ins stay on gameplay only.
        fx, fy, fw, fh = (int(round(float(v))) for v in facecam)
        if src_w and src_h:
            src_w, src_h = int(src_w), int(src_h)
            if fx >= src_w or fy >= src_h:
                fw = fh = 0
            else:
                fw, fh = min(fw, src_w - fx), min(fh, src_h - fy)
        if fw > 8 and fh > 8:
            motion = _motion_expr(plan, W, SPLIT_H) if applied else None
            game_crop = _split_game_crop(src_w, src_h, facecam) if src_w and src_h else (0, 0, 0, 0)
            gx, gy, gw, gh = game_crop
            if gw > 0 and gh > 0:
                game_chain = (f"crop={gw}:{gh}:{gx}:{gy},scale={W}:{SPLIT_H}:force_original_aspect_ratio=increase,"
                              f"crop={W}:{SPLIT_H},setsar=1")
            else:
                game_chain = f"scale={W}:{SPLIT_H}:force_original_aspect_ratio=increase,crop={W}:{SPLIT_H},setsar=1"
            if motion:
                game_chain += "," + motion
            game_chain += grade
            cam_grade = ",eq=contrast=1.05:saturation=1.06,unsharp=3:3:0.35:3:3:0.0" if applied else ""
            streamer_chain = (f"crop={fw}:{fh}:{fx}:{fy},scale={W}:{SPLIT_H}:force_original_aspect_ratio=increase,"
                              f"crop={W}:{SPLIT_H},setsar=1{cam_grade}")
            return (prefix
                    + f"{vpad}split=2[cam0][game0];"
                    + f"[cam0]{streamer_chain}[streamer];"
                    + f"[game0]{game_chain}[game];"
                    + f"[streamer][game]vstack=inputs=2,drawbox=x=0:y={SPLIT_H - 2}:w={W}:h=4:color=white@0.9:t=fill,setsar=1[v1];")

    if layout == "gameplay" and _has_facecam(facecam):
        # Facecam-safe composition: motion & impact shake run ONLY on the gameplay layer [b]; the webcam [p]
        # stays crisp, bordered, and completely uncropped in the top-right safe margin.
        fx, fy, fw, fh = (int(v) for v in facecam)
        pip_h = max(2, int(round(PIP_W * fh / fw / 2)) * 2)
        motion = _motion_expr(plan, W, H) if applied else None
        b_chain = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
        if motion:
            b_chain += "," + motion
        b_chain += grade
        cam_grade = ",eq=contrast=1.05:saturation=1.06,unsharp=3:3:0.35:3:3:0.0" if applied else ""
        cam_chain = (f"crop={fw}:{fh}:{fx}:{fy},scale={PIP_W}:{pip_h},setsar=1{cam_grade},"
                     f"pad={PIP_W + 2 * PIP_BORDER}:{pip_h + 2 * PIP_BORDER}:{PIP_BORDER}:{PIP_BORDER}:color=black@0.55")
        ox = W - (PIP_W + 2 * PIP_BORDER) - PIP_MARGIN
        return (prefix
                + f"{vpad}split=2[base][fc];"
                + f"[base]{b_chain}[b];"
                + f"[fc]{cam_chain}[p];"
                + f"[b][p]overlay={ox}:{PIP_MARGIN}[v1];")

    if layout in ("crop", "gameplay", "split"):
        motion = _motion_expr(plan, W, H) if applied else None
        chain = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
        if motion:
            chain += "," + motion
        chain += grade
        return f"{prefix}{vpad}{chain}[v1];"

    # Blur / Zoom layouts: darkened blurred cinema backdrop behind a framed foreground; motion runs inside [fg]
    motion = _motion_expr(plan, W, fg_h) if applied else None
    fg_chain = f"scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h},setsar=1"
    if motion:
        fg_chain += "," + motion
    bg_eq = "eq=brightness=-0.18:saturation=0.88,vignette=PI/4.8" if applied else "eq=brightness=-0.14:saturation=1.15"
    return (prefix
            + f"{vpad}split=2[a][b];"
            + f"[a]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur=7:2,"
            + f"scale={W}:{H},{bg_eq}[bg];"
            + f"[b]{fg_chain}[fg];"
            + f"[bg][fg]overlay=0:(H-h)/2,setsar=1{grade}[v1];")


def _edit_video(beats_or_plan) -> str:
    """Compatibility helper for building video edit filter segment from a beat list or full plan."""
    if isinstance(beats_or_plan, dict):
        plan = beats_or_plan
    else:
        beats = list(beats_or_plan or [])
        if not beats:
            return "[v0]null[v1];"
        plan = {
            "applied": True,
            "opening_intro": None,
            "color_grade": _PRESET_CFG["pro"],
            "beats": [{"at": float(t), "zoom": 1.24, "hold": 1.2, "ease_in": 0.14, "ease_out": 0.24,
                       "pan_x": 0.0, "pan_y": 0.0} for t in beats],
        }
    if not plan.get("applied"):
        return "[v0]null[v1];"
    motion = _motion_expr(plan, W, H)
    grade = _grade_chain(plan)
    parts = [p for p in (motion, grade.lstrip(",")) if p]
    if not parts:
        return "[v0]null[v1];"
    return f"[v0]{','.join(parts)}[v1];"


def _sfx_chain(kind: str, idx: int, delay_ms: int, gain: float) -> str:
    """Synthesize layered cinematic short-form SFX (sub-bass impact hit, airy whoosh, tactile pop) via FFmpeg."""
    if kind == "impact":
        # Pro climax impact hit: deep sub-bass drop (130Hz -> 42Hz) + mid transient punch + crisp filtered transient, 320ms
        amp = round(0.30 * gain, 3)
        return (f"aevalsrc=exprs='({amp}*exp(-10*t)*sin(2*PI*(42+88*exp(-18*t))*t)"
                f"+{amp * 0.42:.3f}*exp(-26*t)*sin(2*PI*220*t)"
                f"+{amp * 0.18:.3f}*exp(-45*t)*sin(2*PI*740*t))':s=48000:d=0.34:c=stereo,"
                f"lowpass=f=2400,afade=t=in:st=0:d=0.005,afade=t=out:st=0.20:d=0.14,"
                f"adelay={delay_ms}|{delay_ms}[sfx{idx}];")
    if kind == "accent":
        # Balanced punch-in accent: warm low-mid thump (150Hz -> 55Hz) + soft harmonic, 230ms
        amp = round(0.24 * gain, 3)
        return (f"aevalsrc=exprs='({amp}*exp(-15*t)*sin(2*PI*(55+95*exp(-20*t))*t)"
                f"+{amp * 0.30:.3f}*exp(-26*t)*sin(2*PI*310*t))':s=48000:d=0.24:c=stereo,"
                f"afade=t=in:st=0:d=0.007,afade=t=out:st=0.15:d=0.09,"
                f"adelay={delay_ms}|{delay_ms}[sfx{idx}];")
    if kind == "pop":
        # Reaction tactile pop: crisp upward harmonic pop, 130ms
        amp = round(0.22 * gain, 3)
        return (f"aevalsrc=exprs='({amp}*exp(-25*t)*(sin(2*PI*(340+210*t*6)*t)+0.38*sin(2*PI*690*t)))':"
                f"s=48000:d=0.14:c=stereo,afade=t=in:st=0:d=0.004,afade=t=out:st=0.08:d=0.06,"
                f"adelay={delay_ms}|{delay_ms}[sfx{idx}];")
    # Default: airy cinematic stereo whoosh (filtered pink noise swell, 270ms)
    vol = round(0.26 * gain, 3)
    return (f"anoisesrc=c=pink:r=48000:d=0.27,highpass=f=220,lowpass=f=2200,"
            f"afade=t=in:st=0:d=0.09:curve=qsin,afade=t=out:st=0.10:d=0.17:curve=qsin,"
            f"volume={vol},aformat=channel_layouts=stereo,adelay={delay_ms}|{delay_ms}[sfx{idx}];")


def _jump_cut_audio_prefix(plan: dict) -> tuple[str, str]:
    """Build frame-exact jump-cut atrim+micro-fade+concat prefix matching video jump-cuts (zero clicks or A/V drift)."""
    segs = (plan.get("jump_cuts") or {}).get("segments") or []
    base = "[0:a:0]aresample=48000,asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo"
    if len(segs) <= 1:
        return f"{base}[ain];", "[ain]"
    parts = [f"{base},asplit={len(segs)}" + "".join(f"[as{i}]" for i in range(len(segs))) + ";"]
    for i, (s0, e0) in enumerate(segs):
        seg_len = max(0.05, e0 - s0)
        fades = []
        if i > 0:
            fades.append("afade=t=in:st=0:d=0.012")
        if i < len(segs) - 1 and seg_len > 0.04:
            fades.append(f"afade=t=out:st={max(0.0, seg_len - 0.012):.3f}:d=0.012")
        fade_str = ("," + ",".join(fades)) if fades else ""
        parts.append(f"[as{i}]atrim=start={s0:.3f}:end={e0:.3f},asetpts=PTS-STARTPTS{fade_str}[at{i}];")
    parts.append("".join(f"[at{i}]" for i in range(len(segs))) + f"concat=n={len(segs)}:v=0:a=1[ain];")
    return "".join(parts), "[ain]"


def _voice_master_chain(studio: bool) -> str:
    """Studio dialogue mastering chain: rumble removal, warmth + vocal clarity EQ, broadcast compression, -14 LUFS."""
    if not studio:
        return "loudnorm=I=-14:TP=-1.5:LRA=11"
    return ("highpass=f=75,"
            "equalizer=f=160:t=q:w=1.2:g=1.6,"
            "equalizer=f=3400:t=q:w=1.1:g=2.4,"
            "acompressor=threshold=-20dB:ratio=3:attack=8:release=90:makeup=2.2,"
            "loudnorm=I=-14:TP=-1.5:LRA=11")


def _edit_audio(beats_or_plan) -> str:
    """Apply jump-cut sync, studio vocal mastering, and layered cinematic SFX with brickwall limiting."""
    if isinstance(beats_or_plan, dict):
        plan = beats_or_plan
        style = plan.get("style", "pro")
        gain = _PRESET_CFG.get(style, _PRESET_CFG["pro"])["sfx_gain"]
        studio = bool(plan.get("applied") and plan.get("studio_voice", style != "off"))
        events = []
        intro = plan.get("opening_intro")
        if intro and intro.get("sound"):
            events.append((0.04, "whoosh"))
        for b in plan.get("beats") or []:
            if b.get("sound"):
                events.append((max(0.02, float(b["at"]) - 0.04), str(b["sound"])))
        prefix, apad = _jump_cut_audio_prefix(plan) if plan.get("applied") else (
            "[0:a:0]aresample=48000,asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo[ain];", "[ain]")
    else:
        gain = 1.0
        studio = True
        events = [(max(0.02, float(t) - 0.04), "impact") for t in (beats_or_plan or [])]
        prefix, apad = "[0:a:0]aresample=48000,asetpts=PTS-STARTPTS,aformat=channel_layouts=stereo[ain];", "[ain]"

    master = _voice_master_chain(studio)
    if not events:
        lim = ",alimiter=limit=0.95:attack=5:release=50" if studio else ""
        return f"{prefix}{apad}{master}{lim}[a]"

    parts = [f"{prefix}{apad}{master}[voice];"]
    for i, (at_t, kind) in enumerate(events):
        delay_ms = max(0, round(at_t * 1000))
        parts.append(_sfx_chain(kind, i, delay_ms, gain))
    inputs = "[voice]" + "".join(f"[sfx{i}]" for i in range(len(events)))
    parts.append(f"{inputs}amix=inputs={len(events) + 1}:duration=first:dropout_transition=0:normalize=0,"
                 "alimiter=limit=0.95:attack=5:release=50[a]")
    return "".join(parts)


_qsv_ok = None
RENDER_SECONDS = 600      # one clip's video encode; far beyond normal, so a frozen encoder is stopped, not waited on


def _has_audio_stream(src: Path) -> bool:
    try:
        p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
                            "-of", "csv=p=0", str(src)],
                           capture_output=True, text=True, creationflags=NO_WINDOW, timeout=20)
        return bool((p.stdout or "").strip())
    except Exception:  # noqa: BLE001
        return True


def _make_watermark_png(path: Path, text: str) -> tuple[int, int]:
    """Draw a small, high-contrast channel sticker that stays legible over any footage."""
    from PIL import Image, ImageDraw, ImageFont

    text = branding.clean_watermark_text(text)
    if not text:
        raise ValueError("Add banner text before showing the channel banner.")

    scale = max(0.5, W / 1080)
    height = max(44, int(round(88 * scale)))
    badge = int(round(46 * scale))
    pad_left, gap, pad_right = (int(round(x * scale)) for x in (15, 12, 20))
    margin_x = max(16, int(round(W * 0.045)))
    max_width = max(80, W - 2 * margin_x)
    available = max(24, max_width - pad_left - badge - gap - pad_right)

    font_dir = Path(__file__).resolve().parent / "fonts"
    try:
        main_font = ImageFont.truetype(str(font_dir / "Poppins-ExtraBold.ttf"), max(14, int(round(34 * scale))))
        small_font = ImageFont.truetype(str(font_dir / "Poppins-ExtraBold.ttf"), max(8, int(round(13 * scale))))
        badge_font = ImageFont.truetype(str(font_dir / "Poppins-ExtraBold.ttf"), max(12, int(round(25 * scale))))
    except OSError:
        main_font = small_font = badge_font = ImageFont.load_default()

    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))

    def width_of(value, font):
        box = probe.textbbox((0, 0), value, font=font)
        return box[2] - box[0]

    shown = text
    if width_of(shown, main_font) > available:
        while shown and width_of(shown + "…", main_font) > available:
            shown = shown[:-1]
        shown = shown.rstrip() + "…"
    text_width = width_of(shown, main_font)
    width = min(max_width, pad_left + badge + gap + text_width + pad_right)
    image = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    radius = int(round(20 * scale))
    draw.rounded_rectangle((0, 0, width - 1, height - 1), radius=radius,
                           fill=(17, 17, 21, 232), outline=(255, 255, 255, 205), width=max(1, int(round(2 * scale))))

    badge_x = pad_left
    badge_y = (height - badge) // 2
    draw.ellipse((badge_x, badge_y, badge_x + badge, badge_y + badge), fill=(255, 116, 75, 255))
    icon_box = draw.textbbox((0, 0), "@", font=badge_font)
    icon_w, icon_h = icon_box[2] - icon_box[0], icon_box[3] - icon_box[1]
    draw.text((badge_x + (badge - icon_w) / 2 - icon_box[0],
               badge_y + (badge - icon_h) / 2 - icon_box[1] - 1 * scale),
              "@", font=badge_font, fill=(255, 255, 255, 255))

    text_x = badge_x + badge + gap
    label_y, main_y = int(round(11 * scale)), int(round(32 * scale))
    draw.text((text_x, label_y), "CHANNEL", font=small_font, fill=(255, 157, 126, 255))
    draw.text((text_x, main_y), shown, font=main_font, fill=(255, 255, 255, 255))
    image.save(path, format="PNG", optimize=True)
    return image.size


def _watermark_coords(position: str, width: int, height: int) -> tuple[int, int]:
    """Inset the banner from the frame edges, away from the bottom progress bar."""
    if position not in branding.WATERMARK_POSITION_LABELS:
        position = branding.DEFAULT_WATERMARK_POSITION
    mx = max(16, int(round(W * 0.045)))
    my = max(20, int(round(H * 0.035)))
    x = mx if position.endswith("left") else max(0, W - mx - width)
    y = my if position.startswith("top") else max(0, H - my - height)
    return x, y


def render_clip(src: Path, out_dir: Path, name: str, clip: dict, words: list, src_w: int, src_h: int) -> dict:
    """Writes <name>.mp4, <name>.jpg and <name>.ass into out_dir. Returns file names, edit_points, and edit_summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    start, end = clip["start"], clip["end"]
    face = clip.get("facecam")
    original_layout = clip.get("layout", "blur")
    requested_layout = original_layout
    layout_fallback = ""
    if requested_layout in ("gameplay", "split") and not _has_facecam(face):
        requested_layout = "crop"  # without a detectable webcam, fill the screen with the game instead
        label = LAYOUTS.get(original_layout, original_layout)
        layout_fallback = f"No webcam was detected. Full-screen gameplay was used instead of {label}."
    layout, fg_h, hook_y, cap_y = geometry(src_w, src_h, requested_layout)
    if not layout_fallback and layout != requested_layout:
        label = LAYOUTS.get(original_layout, original_layout)
        layout_fallback = f"The source is already vertical. Full-screen framing was used instead of {label}."
    hook_y = _hook_y(layout, hook_y, face)

    plan = plan_edits(clip, words, src=src, layout=layout, facecam=face)
    beats = [b["at"] for b in plan["beats"]]
    segs = (plan.get("jump_cuts") or {}).get("segments") or []
    eff_end = start + float(plan.get("effective_dur") or (end - start))
    synced_words = remap_words(words, start, segs) if len(segs) > 1 else words
    ass = build_ass(synced_words if clip.get("captions", True) else [], start, eff_end, clip.get("hook", ""),
                    clip.get("style", "bounce"), clip.get("highlight_words", []), hook_y, cap_y,
                    edit_style=plan["style"])

    # Short temp folder keeps ffmpeg's libass filter path safe on Windows & avoids colon escaping issues.
    # Render inside the data/tmp folder (a controlled short path) instead of the system temp dir, so a deep
    # Windows temp path never exceeds the 260-character limit and breaks libass filter paths.
    work = Path(tempfile.mkdtemp(prefix="pk", dir=config.TMP_DIR))
    try:
        watermark = None
        watermark_text = clip.get("watermark_text")
        if clip.get("watermark_enabled") and isinstance(watermark_text, str):
            watermark_text = branding.clean_watermark_text(watermark_text)
            if watermark_text:
                watermark = work / "watermark.png"
                _make_watermark_png(watermark, watermark_text)
        result = _render_in(work, src, out_dir, name, start, end, layout, fg_h, ass, face=face,
                            beats=beats, sound_fx=plan["sound_active"], plan=plan, src_w=src_w, src_h=src_h,
                            watermark=watermark, watermark_position=clip.get("watermark_position", branding.DEFAULT_WATERMARK_POSITION))
        return {
            **result,
            "layout_requested": original_layout,
            "layout_fallback": layout_fallback,
            "edit_points": beats,
            "edit_style": plan["style"],
            "sound_fx": plan["sound_fx"],
            "edit_summary": plan["summary"],
        }
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _render_in(work: Path, src: Path, out_dir: Path, name: str, start, end, layout, fg_h, ass: str,
               face=None, beats=None, sound_fx=True, plan=None, src_w=None, src_h=None,
               watermark: Path | None = None, watermark_position: str = branding.DEFAULT_WATERMARK_POSITION) -> dict:
    global _qsv_ok
    (work / f"{name}.ass").write_text(ass, encoding="utf-8")
    (work / "f").mkdir()
    for f in config.FONTS_DIR.glob("*.ttf"):
        shutil.copyfile(f, work / "f" / f.name)
    beats = beats or []
    dur = max(0.2, float(end) - float(start))
    if plan is None:
        plan = {
            "style": "pro" if beats else "off",
            "applied": bool(beats),
            "sound_active": bool(sound_fx and beats),
            "studio_voice": bool(beats),
            "effective_dur": dur,
            "opening_intro": None,
            "color_grade": _PRESET_CFG["pro"] if beats else None,
            "beats": [{"at": float(t), "role": "payoff", "zoom": 1.28, "hold": 1.2, "ease_in": 0.14,
                       "ease_out": 0.22, "pan_x": 0.0, "pan_y": 0.0, "sound": "impact" if sound_fx else None}
                      for t in beats],
        }
    out_dur = max(0.2, float(plan.get("effective_dur") or dur))
    has_audio = _has_audio_stream(src)
    # Strip SFX events cleanly if sound_fx=False while preserving studio vocal mastering & jump-cut sync!
    audio_plan = dict(plan)
    if not sound_fx or not plan.get("sound_active"):
        audio_plan["opening_intro"] = None
        audio_plan["beats"] = []

    if watermark:
        from PIL import Image
        with Image.open(watermark) as sticker:
            wm_w, wm_h = sticker.size
        wm_x, wm_y = _watermark_coords(watermark_position, wm_w, wm_h)
        fc = (_filter(layout, fg_h, face, plan=plan, src_w=src_w, src_h=src_h)
              + f"[v1]ass={name}.ass:fontsdir=f[v2];[v2][1:v]overlay={wm_x}:{wm_y}:format=auto:eof_action=repeat[v]")
    else:
        fc = _filter(layout, fg_h, face, plan=plan, src_w=src_w, src_h=src_h) + f"[v1]ass={name}.ass:fontsdir=f[v]"
    if has_audio:
        fc += ";" + _edit_audio(audio_plan)

    base = ["ffmpeg", "-y", "-v", "error", "-ss", f"{float(start):.2f}", "-t", f"{dur:.2f}", "-i", str(src)]
    if watermark:
        base += ["-loop", "1", "-framerate", str(config.OUT_FPS), "-i", watermark.name]
    base += ["-filter_complex", fc, "-map", "[v]"]
    if has_audio:
        base += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        base += ["-an"]
    base += ["-t", f"{out_dur:.2f}", "-movflags", "+faststart", "-pix_fmt"]

    encoders = []
    if _qsv_ok is not False:
        encoders.append(("qsv", ["nv12", "-c:v", "h264_qsv", "-global_quality", "22", "-preset", "fast"]))
    encoders.append(("x264", ["yuv420p", "-c:v", "libx264", "-crf", "20", "-preset", "veryfast"]))
    err = None
    for label, enc in encoders:
        p = subprocess.run(base + enc + [f"{name}.mp4"], cwd=work, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", creationflags=NO_WINDOW, timeout=RENDER_SECONDS)
        if p.returncode == 0:
            if label == "qsv":
                _qsv_ok = True
            break
        err = p.stderr[-1200:]
        if label == "qsv":
            _qsv_ok = False
    else:
        raise RuntimeError(f"Render failed: {err}")
    cover_t = min(1.2, out_dur / 2)
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{cover_t:.2f}", "-i", f"{name}.mp4", "-frames:v", "1",
         "-q:v", "3", f"{name}.jpg"], cwd=work)
    for ext in ("mp4", "jpg", "ass"):
        shutil.move(str(work / f"{name}.{ext}"), str(out_dir / f"{name}.{ext}"))
    return {"file": f"{name}.mp4", "cover": f"{name}.jpg", "layout": layout}
