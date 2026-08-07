"""Render VISION App's overlay to PNGs — no camera, no cursor, no key events.

Every button, glow and lock state painted onto a synthetic camera frame, so the
interface can be reviewed (and put in the README) without opening a webcam or moving
anybody's mouse.

    python tools/render.py [out_dir]        # default: docs/images

ONE controller, reused for every scene. VisionController.__init__ builds a MediaPipe
graph, and constructing a handful of them in one process is a good way to take the
interpreter down — so it is built once here and its state is reset between scenes.
"""
from __future__ import annotations

import sys
from collections import deque
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import vision as V  # noqa: E402

W, H = 960, 720
NOW = 1000.0


def backdrop() -> np.ndarray:
    """Something camera-like to draw over: a soft vignette plus sensor grain, so the
    overlay's translucency is judged against texture rather than flat black."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    r = np.hypot(xx - W / 2, yy - H / 2) / np.hypot(W / 2, H / 2)
    base = (58 * (1.0 - 0.72 * r))[..., None] * np.array([1.20, 1.02, 0.88], np.float32)
    grain = np.random.default_rng(11).normal(0, 4, (H, W, 1))
    return np.clip(base + grain, 0, 255).astype(np.uint8)


def arc_trail(cx: float, cy: float, n: int = 18, radius: float = 0.11):
    """A quarter-circle of pointer samples, oldest first — enough for the comet."""
    import math
    out = []
    for i, a in enumerate(np.linspace(3.3, 1.5, n)):
        age = (1 - i / (n - 1)) * (V.TRAIL_FADE_S * 0.9)
        out.append((cx + radius * math.cos(a), cy - radius * math.sin(a),
                    NOW - age, 0.45))
    return deque(out, maxlen=V.TRAIL_MAXLEN)


_RESET = dict(
    active=True, mouse_mode=False, alttab_mode=False,
    current_zone=V.Zone.NONE, zone_hold_count=0, zone_triggered=False,
    scr_locked=False, scr_hold=0, scr_countdown=None,
    vol_locked=False, vol_hold=0, vol_countdown=None,
)


def scene(v, name: str, **state):
    v.__dict__.update(_RESET)
    v.tap_frames = {}
    v.gesture_trail = deque(maxlen=30)
    v.trail = deque(maxlen=V.TRAIL_MAXLEN)
    v.__dict__.update({k: val for k, val in state.items() if not k.startswith("_")})
    frame = backdrop()
    v._draw_hud(frame, "", state.get("_label", ""), NOW)
    return name, frame


def build_scenes(v):
    hover = deque([(V.BTN_TOP_C3_X, V.BTN_TOP_C3_Y + 0.03, NOW)], maxlen=30)
    return [
        scene(v, "1-standby", active=False),
        scene(v, "2-buttons"),
        scene(v, "3-tap-hover", gesture_trail=hover,
              current_zone=V.Zone.TOP_C3, tap_frames={V.Zone.TOP_C3: 1},
              _label="NEXT TAB"),
        scene(v, "4-volume-locked", vol_locked=True, vol_hold=5,
              vol_countdown=NOW + 1.7,
              gesture_trail=deque([(V.VOL_BTN_X, 0.44, NOW)], maxlen=30),
              _label="VOL UP"),
        scene(v, "5-scroll-locked", scr_locked=True, scr_hold=5,
              scr_countdown=NOW + 2.1,
              gesture_trail=deque([(V.SCR_BTN_X, 0.60, NOW)], maxlen=30),
              _label="SCROLL DOWN"),
        scene(v, "6-mouse-trail", mouse_mode=True, trail=arc_trail(0.55, 0.46),
              _label="CLICK"),
        scene(v, "7-alttab", alttab_mode=True, _label="NEXT WINDOW"),
    ]


def main(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    v = V.VisionController(show_window=False)
    for name, frame in build_scenes(v):
        cv2.imwrite(str(out / f"{name}.png"), frame)
        print(f"  {name}.png")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1
         else Path(__file__).resolve().parent.parent / "docs" / "images")
