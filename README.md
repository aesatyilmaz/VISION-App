# VISION AI

A hand gesture control system for Windows built with MediaPipe. Control your computer — scroll, adjust volume, switch windows, move the mouse, and trigger custom actions — all without touching your keyboard or mouse.

---

## Demo

| Gesture | Action |
|---|---|
| ✋ Open palm, move up/down near scroll button | Scroll — speed scales with hand velocity |
| 🤏 Pinch near volume button | Volume up/down — dynamic speed |
| 👉 Swipe right (one hand) | Next window (Alt+Tab) |
| 👈 Swipe left (one hand) | Previous window (Alt+Shift+Tab) |
| 👌 OK sign, hold 1s | Open Alt+Tab switcher |
| ✌️ Two hands still for 2s | Close Alt+Tab switcher |
| 🤚 Two hands swipe down | Custom action (5s cooldown protection) |
| ☝️ Index tap on button zones | Trigger media/custom buttons |
| 🖱️ Index tip in center zone | Mouse control with smoothing |
| 👊 Closed fist | Lock (2.5s countdown) |

---

## Features

- **9 tap button zones** — top row (4), sides (2), bottom row (3) — fully customizable
- **Dynamic scroll speed** — hand velocity → scroll amount (min 50, max 650)
- **Dynamic volume control** — velocity-based, min 1 / max 20 steps
- **Alt+Tab window management** — open, cycle, close without keyboard
- **Mouse mode** — 60% center zone, 0.28 smoothing, click via finger pinch
- **Mouse trail effect** — visual feedback with fade (25-frame trail)
- **Zone Glow** — buttons light up when hand enters the zone
- **Lock gesture** — closed fist locks all input, 2.5s countdown
- **Two-hand down protection** — 5s cooldown prevents accidental triggers
- **Multi-monitor support** — uses virtual screen dimensions (SM_XVIRTUALSCREEN)

---

## How It Works

```
Webcam feed
    ↓
MediaPipe Hands (landmark detection)
    ↓
Gesture classifier (swipe, tap, ok, fist, two-hand)
    ↓
Zone detector (which button region is the hand in?)
    ↓
Action executor (pyautogui / keyboard)
    ↓
Overlay renderer (cv2 — buttons, glow, trail, debug)
```

### Coordinate System

All positions are **normalized (0.0–1.0)** relative to the camera frame — resolution-independent. Button positions are defined as constants and can be repositioned without touching logic.

### Gesture Detection

- **Swipe:** tracks palm X position over the last 20 frames, triggers if Δx > 0.12
- **OK sign:** thumb tip and index tip distance < threshold, held for 1s
- **Fist (lock):** all finger curl values > threshold
- **Two-hand still:** both palms tracked, movement < 0.03 normalized units for 2s
- **Tap:** index fingertip enters button zone rect/circle, triggers in 2 frames

---

## Getting Started

### Requirements

- Python 3.10+
- Windows 10/11
- Webcam

### Install

```bash
pip install opencv-python mediapipe pyautogui keyboard
```

### Run

```bash
python vision.py
```

Press **Q** to quit.

---

## Project Structure

```
vision/
├── vision.py      ← Main module (gesture engine + overlay)
└── __init__.py
```

All logic lives in `vision.py` — gesture classification, zone detection, action execution, and the OpenCV overlay renderer are intentionally kept in one file for portability.

---

## Configuration

Key constants at the top of `vision.py`:

```python
COOLDOWN         = 1.2    # seconds between gesture triggers
SWIPE_THRESHOLD  = 0.12   # normalized swipe distance
SCROLL_MIN       = 50     # minimum scroll amount
SCROLL_MAX       = 650    # maximum scroll amount
MOUSE_SMOOTHING  = 0.28   # mouse cursor smoothing (0=raw, 1=frozen)
LOCK_COUNTDOWN   = 2.5    # seconds to show lock countdown
```

Button positions (normalized, 0.0–1.0):
```python
BTN_TOP_C1_X, BTN_TOP_C1_Y = 0.15, 0.07   # top-left button
BTN_TOP_C4_X, BTN_TOP_C4_Y = 0.85, 0.07   # top-right button
# ... etc
```

---

## Tech Stack

| Library | Purpose |
|---|---|
| [MediaPipe](https://mediapipe.dev) | Hand landmark detection (21 points per hand) |
| [OpenCV](https://opencv.org) | Webcam capture + overlay rendering |
| [pyautogui](https://pyautogui.readthedocs.io) | Mouse control, scrolling |
| [keyboard](https://github.com/boppreh/keyboard) | Hotkey simulation |

---

*Built with ❤️ by Esat*
