"""
VISION App — hand-gesture computer control for Windows (MediaPipe + OpenCV).

Everything lives in this one file on purpose — gesture classification, zone detection,
action execution and the OpenCV overlay — so the whole thing is one import away.

- Tap buttons, triggered by the index fingertip
- Scroll and volume: speed follows how fast your hand moves, not a fixed step
- Zone glow: a button lights up as your hand approaches it
- Gestures: swipe left/right, two hands down
- Alt+Tab window switcher:
    OK sign, held 1s        open it (Alt stays down)
    one-hand swipe right    next window (Tab)
    one-hand swipe left     previous window (Shift+Tab)
    both hands still, 2s    close it (Alt released)
- Two-hand-down is blocked for 5s after any other action, so it cannot fire by accident
- Locking a fader shows a 2s countdown before it takes hold
"""

import cv2
import mediapipe as mp
import pyautogui
import keyboard
import time
import math
import threading
from collections import deque
from enum import Enum, auto

pyautogui.FAILSAFE = False
pyautogui.PAUSE = 0.0

# ─── Sabitler ──────────────────────────────────────────────────────────────────
COOLDOWN             = 1.2
SWIPE_FRAMES         = 20
SWIPE_THRESHOLD      = 0.12
TWO_HAND_THRESHOLD   = 0.10

BTN_RECT_W           = 0.11
BTN_RECT_H           = 0.09
BTN_CIRCLE_R         = 0.07
SCR_BTN_R            = 0.085

# Tap: isaret parmagi ucuyla, kac frame icinde tetiklenir
TAP_FRAMES           = 2

# Buton pozisyonlari
BTN_TOP_C1_X, BTN_TOP_C1_Y  = 0.15, 0.07
BTN_TOP_C2_X, BTN_TOP_C2_Y  = 0.38, 0.07
BTN_TOP_C3_X, BTN_TOP_C3_Y  = 0.62, 0.07
BTN_TOP_C4_X, BTN_TOP_C4_Y  = 0.85, 0.07
VOL_BTN_X,    VOL_BTN_Y     = 0.07, 0.50
SCR_BTN_X,    SCR_BTN_Y     = 0.93, 0.50
BTN_BOT_C1_X, BTN_BOT_C1_Y = 0.32, 0.93
BTN_BOT_C2_X, BTN_BOT_C2_Y = 0.52, 0.93
BTN_BOT_C3_X, BTN_BOT_C3_Y = 0.70, 0.93

# Lock
LOCK_COUNTDOWN       = 2.5
LOCK_GESTURE_BLOCK   = 2.0   # lock olduktan sonra kac sn gesture inaktif

# Alt+Tab
TWO_HAND_STILL_SECS  = 2.0   # iki el duragan kac sn = kapat
TWO_HAND_STILL_THRESH= 0.03  # hareket esigi (normalize)
OK_HOLD_SECS         = 1.0   # OK isareti kac sn = Alt+Tab ac

# Two-hand down koruma
TWO_HAND_DOWN_LOCK   = 5.0   # son aksiyondan kac sn sonra calisir

# Scroll - dinamik hiz bazli (v6 ile ayni)
SCROLL_MIN           = 50    # minimum scroll
SCROLL_MAX           = 650   # maksimum scroll
SCROLL_SPEED_MULT    = 2200  # hiz carpani
SCROLL_MOVE_THRESH   = 0.008

# Volume - dinamik hiz bazli
VOL_MIN              = 1     # minimum bas sayisi
VOL_MAX              = 20    # maksimum bas sayisi
VOL_MULT             = 140    # hiz carpani
VOL_MOVE_THRESH      = 0.015

# Hold (media butonlari)
ZONE_HOLD_FRAMES     = 12

# Mouse
MOUSE_CAM_ZONE       = 0.60
MOUSE_CAM_MARGIN     = (1.0 - MOUSE_CAM_ZONE) / 2
MOUSE_SMOOTHING      = 0.28
CLICK_COOLDOWN       = 0.7
CLICK_FRAMES_NEEDED  = 3
MOUSE_EXIT_TOLERANCE = 8

# Zone Glow
GLOW_RADIUS          = 0.18

# Mouse Trail (v2.0)
TRAIL_MAXLEN         = 25
TRAIL_FADE_S         = 0.5

import ctypes as _ctypes
_u32     = _ctypes.windll.user32
SCREEN_X = _u32.GetSystemMetrics(76)   # SM_XVIRTUALSCREEN
SCREEN_Y = _u32.GetSystemMetrics(77)   # SM_YVIRTUALSCREEN
SCREEN_W = _u32.GetSystemMetrics(78)   # SM_CXVIRTUALSCREEN
SCREEN_H = _u32.GetSystemMetrics(79)   # SM_CYVIRTUALSCREEN
BTN_ALPHA            = 0.58

WRIST      = 0
THUMB_TIP  = 4;  THUMB_IP   = 3;  THUMB_MCP  = 2
INDEX_TIP  = 8;  INDEX_PIP  = 6;  INDEX_MCP  = 5
MIDDLE_TIP = 12; MIDDLE_PIP = 10; MIDDLE_MCP = 9
RING_TIP   = 16; RING_PIP   = 14; RING_MCP   = 13
PINKY_TIP  = 20; PINKY_PIP  = 18; PINKY_MCP  = 17


class Zone(Enum):
    NONE    = auto()
    TOP_C1  = auto()
    TOP_C2  = auto()
    TOP_C3  = auto()
    TOP_C4  = auto()
    VOL_BTN = auto()
    SCR_BTN = auto()
    BOT_C1  = auto()
    BOT_C2  = auto()
    BOT_C3  = auto()


class Gesture(Enum):
    NONE            = auto()
    SWIPE_LEFT      = auto()
    SWIPE_RIGHT     = auto()
    TWO_HAND_DOWN   = auto()


def dist(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)

def dist_2d(x1, y1, x2, y2):
    return math.hypot(x1-x2, y1-y2)

def finger_curl(lm, tip, mcp):
    td = dist(lm[tip], lm[WRIST])
    md = dist(lm[mcp], lm[WRIST])
    if md == 0: return 0.0
    return max(0.0, min(1.0, 1.5 - td/md))

def palm_center(lm):
    pts = [WRIST, INDEX_MCP, MIDDLE_MCP, RING_MCP, PINKY_MCP]
    return sum(lm[i].x for i in pts)/5, sum(lm[i].y for i in pts)/5

def is_ok(lm):
    """OK isareti: basparmak + isaret parmagi ucu birbirine yakin, diger 3 parmak acik."""
    thumb_index_dist = dist(lm[THUMB_TIP], lm[INDEX_TIP])
    # Olcegi normalize etmek icin el boyutuna gore
    hand_size = dist(lm[WRIST], lm[MIDDLE_MCP])
    if hand_size == 0: return False
    ratio = thumb_index_dist / hand_size
    touching = ratio < 0.25
    # Diger parmaklar acik olmali
    middle_open = lm[MIDDLE_TIP].y < lm[MIDDLE_PIP].y
    ring_open   = lm[RING_TIP].y   < lm[RING_PIP].y
    pinky_open  = lm[PINKY_TIP].y  < lm[PINKY_PIP].y
    return touching and middle_open and ring_open and pinky_open

def is_peace(lm):
    return (lm[INDEX_TIP].y < lm[INDEX_PIP].y and
            lm[MIDDLE_TIP].y < lm[MIDDLE_PIP].y and
            finger_curl(lm, RING_TIP,  RING_MCP)  > 0.50 and
            finger_curl(lm, PINKY_TIP, PINKY_MCP) > 0.50)

def is_index_only(lm):
    return (lm[INDEX_TIP].y < lm[INDEX_PIP].y and
            finger_curl(lm, MIDDLE_TIP, MIDDLE_MCP) > 0.45 and
            finger_curl(lm, RING_TIP,   RING_MCP)   > 0.50 and
            finger_curl(lm, PINKY_TIP,  PINKY_MCP)  > 0.50)

def in_circle(cx, cy, bx, by, r):
    return dist_2d(cx, cy, bx, by) < r

def in_rect(cx, cy, bx, by, rw, rh):
    return abs(cx-bx) < rw/2 and abs(cy-by) < rh/2

def get_tap_zone(tx, ty):
    """Isaret parmagi ucu ile tap zone tespiti - sadece ust butonlar."""
    if in_rect(tx, ty, BTN_TOP_C1_X, BTN_TOP_C1_Y, BTN_RECT_W, BTN_RECT_H): return Zone.TOP_C1
    if in_rect(tx, ty, BTN_TOP_C2_X, BTN_TOP_C2_Y, BTN_RECT_W, BTN_RECT_H): return Zone.TOP_C2
    if in_rect(tx, ty, BTN_TOP_C3_X, BTN_TOP_C3_Y, BTN_RECT_W, BTN_RECT_H): return Zone.TOP_C3
    if in_rect(tx, ty, BTN_TOP_C4_X, BTN_TOP_C4_Y, BTN_RECT_W, BTN_RECT_H): return Zone.TOP_C4
    return Zone.NONE

def get_palm_zone(cx, cy):
    """Palm center ile diger zone tespiti."""
    if in_circle(cx, cy, VOL_BTN_X, VOL_BTN_Y, SCR_BTN_R): return Zone.VOL_BTN
    if in_circle(cx, cy, SCR_BTN_X, SCR_BTN_Y, SCR_BTN_R): return Zone.SCR_BTN
    if in_circle(cx, cy, BTN_BOT_C2_X, BTN_BOT_C2_Y, BTN_CIRCLE_R): return Zone.BOT_C2
    if in_rect(cx, cy, BTN_BOT_C1_X, BTN_BOT_C1_Y, BTN_RECT_W, BTN_RECT_H): return Zone.BOT_C1
    if in_rect(cx, cy, BTN_BOT_C3_X, BTN_BOT_C3_Y, BTN_RECT_W, BTN_RECT_H): return Zone.BOT_C3
    return Zone.NONE

def calc_swipe(buf, threshold):
    if len(buf) < 8: return "none", 0.0
    pts = list(buf)
    dx = pts[-1][0] - pts[0][0]
    dy = pts[-1][1] - pts[0][1]
    if abs(dy) > abs(dx) and abs(dy) > threshold:
        return ("up" if dy < 0 else "down"), abs(dy)
    if abs(dx) > abs(dy) and abs(dx) > threshold:
        return ("left" if dx < 0 else "right"), abs(dx)
    return "none", 0.0

def cam_to_screen(nx, ny):
    sx = (nx - MOUSE_CAM_MARGIN) / MOUSE_CAM_ZONE
    sy = (ny - MOUSE_CAM_MARGIN) / MOUSE_CAM_ZONE
    return (int(SCREEN_X + max(0.0, min(1.0, sx)) * SCREEN_W),
            int(SCREEN_Y + max(0.0, min(1.0, sy)) * SCREEN_H))


class VisionController:
    def __init__(self, camera_index=0, show_window=True):
        self.camera_index = camera_index
        self.show_window  = show_window

        self.mp_hands = mp.solutions.hands
        self.hands = self.mp_hands.Hands(
            static_image_mode=False, max_num_hands=2,
            min_detection_confidence=0.75, min_tracking_confidence=0.65)
        self.mp_draw = mp.solutions.drawing_utils

        self.active       = False
        self.last_trigger = {}
        self.palm_buf     = deque(maxlen=SWIPE_FRAMES)
        self.two_hand_buf = deque(maxlen=SWIPE_FRAMES)

        # Tap butonlar
        self.tap_frames    = {}
        self.tap_triggered = set()

        # Hold butonlar (media)
        self.zone_hold_count = 0
        self.current_zone    = Zone.NONE
        self.zone_triggered  = False

        # Scroll lock
        self.scr_locked    = False
        self.scr_countdown = None
        self.scr_hold      = 0
        self.scr_last_y    = None

        # Vol lock
        self.vol_locked    = False
        self.vol_countdown = None
        self.vol_hold      = 0
        self.vol_last_y    = None

        # Mouse
        self.mouse_mode        = False
        self.mouse_screen_x    = SCREEN_W // 2
        self.mouse_screen_y    = SCREEN_H // 2
        self.last_click_t      = 0.0
        self.index_frame_cnt   = 0
        self.mouse_exit_frames = 0

        # Alt+Tab window switcher
        self.alttab_mode       = False
        self.two_hand_still_t  = None   # iki el duragan baslangic zamani
        self.two_hand_last_pos = None   # son iki el pozisyonu
        self.ok_hold_start     = None   # OK isareti hold baslangici

        # Son aksiyon zamani (two-hand down koruma)
        self.last_action_t     = 0.0
        self.lock_engaged_t    = 0.0   # en son lock aktif olma zamani

        # Trail
        # Gesture trail (zone glow icin)
        self.gesture_trail = deque(maxlen=30)

        # Mouse Trail (v2.0)
        self.trail         = deque(maxlen=TRAIL_MAXLEN)
        self.trail_last_x  = 0.5
        self.trail_last_y  = 0.5
        self.trail_last_t  = 0.0

        self.cap      = None
        self._thread  = None
        self._running = False

        print("==============================================")
        print("  VISION App")
        print("  V = toggle   |   Q = quit")
        print("==============================================")

    def start(self):
        if self._running: return
        self._running = True
        self._thread  = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False
        if self._thread: self._thread.join(timeout=3)
        if self.cap: self.cap.release()
        cv2.destroyAllWindows()

    def toggle(self):
        self.active = not self.active
        self._reset_all()
        print("VISION App:", "ACTIVE" if self.active else "STANDBY")

    def _reset_all(self):
        self.palm_buf.clear()
        self.two_hand_buf.clear()
        self.tap_frames.clear()
        self.tap_triggered.clear()
        self.zone_hold_count   = 0
        self.current_zone      = Zone.NONE
        self.zone_triggered    = False
        self.scr_locked        = False
        self.scr_countdown     = None
        self.scr_hold          = 0
        self.scr_last_y        = None
        self.vol_locked        = False
        self.vol_countdown     = None
        self.vol_hold          = 0
        self.vol_last_y        = None
        self.mouse_mode        = False
        self.index_frame_cnt   = 0
        self.mouse_exit_frames = 0
        if self.alttab_mode:
            keyboard.release("alt")
            self.alttab_mode = False
        self.two_hand_still_t  = None
        self.two_hand_last_pos = None
        self.ok_hold_start     = None
        self.gesture_trail.clear()
        self.trail.clear()

    def _reset_zone_state(self):
        self.current_zone    = Zone.NONE
        self.zone_hold_count = 0
        self.zone_triggered  = False

    def _run(self):
        self.cap = cv2.VideoCapture(self.camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        self.cap.set(cv2.CAP_PROP_FPS, 30)

        if not self.cap.isOpened():
            print("VISION HATA: Kamera bulunamadi!")
            self._running = False
            return

        last_label = ""

        while self._running:
            ret, frame = self.cap.read()
            if not ret: continue

            frame  = cv2.flip(frame, 1)
            rgb    = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = self.hands.process(rgb)
            action_label = ""
            now = time.time()

            if self.scr_countdown is not None and now > self.scr_countdown:
                self.scr_locked = False; self.scr_countdown = None; self.scr_last_y = None
                print("  -> Scroll UNLOCK")
            if self.vol_countdown is not None and now > self.vol_countdown:
                self.vol_locked = False; self.vol_countdown = None; self.vol_last_y = None
                print("  -> Vol UNLOCK")

            if self.active and result.multi_hand_landmarks:
                num_hands = len(result.multi_hand_landmarks)

                if num_hands >= 2:
                    lm1 = result.multi_hand_landmarks[0].landmark
                    lm2 = result.multi_hand_landmarks[1].landmark
                    gesture, val = self._check_two_hand(lm1, lm2)
                    self.palm_buf.clear()
                    self._reset_zone_state()
                    self.tap_frames.clear()
                    self.tap_triggered.clear()
                    self.mouse_mode        = False
                    self.index_frame_cnt   = 0
                    self.mouse_exit_frames = 0
                    # Lock aktifken two-hand gesture'lari inaktif
                    if gesture != Gesture.NONE and not self.scr_locked and not self.vol_locked:
                        self._execute_gesture(gesture, val)
                        action_label = gesture.name

                else:
                    lm = result.multi_hand_landmarks[0].landmark
                    self.two_hand_buf.clear()
                    cx, cy = palm_center(lm)
                    # Isaret parmagi ucu (tap icin)
                    tx, ty = lm[INDEX_TIP].x, lm[INDEX_TIP].y

                    peace = is_peace(lm)
                    index = is_index_only(lm)

                    if peace:
                        self.mouse_mode        = True
                        self.mouse_exit_frames = 0
                        self.index_frame_cnt   = 0
                        self._reset_zone_state()
                        self.tap_frames.clear()
                        self.tap_triggered.clear()
                        self.palm_buf.clear()

                        sc_x, sc_y = cam_to_screen(tx, ty)
                        self.mouse_screen_x = int(self.mouse_screen_x * MOUSE_SMOOTHING + sc_x * (1 - MOUSE_SMOOTHING))
                        self.mouse_screen_y = int(self.mouse_screen_y * MOUSE_SMOOTHING + sc_y * (1 - MOUSE_SMOOTHING))
                        pyautogui.moveTo(self.mouse_screen_x, self.mouse_screen_y)

                        # Trail: hiz hesapla
                        dt  = max(0.001, now - self.trail_last_t)
                        spd = dist_2d(tx, ty, self.trail_last_x, self.trail_last_y) / dt
                        self.trail.append((tx, ty, now, spd))
                        self.trail_last_x = tx; self.trail_last_y = ty; self.trail_last_t = now

                        action_label = "MOUSE MODE"

                    elif index and (self.mouse_mode or self.mouse_exit_frames > 0):
                        self.index_frame_cnt += 1
                        if self.index_frame_cnt >= CLICK_FRAMES_NEEDED:
                            if now - self.last_click_t > CLICK_COOLDOWN:
                                pyautogui.click()
                                self.last_click_t      = now
                                self.index_frame_cnt   = 0
                                self.mouse_exit_frames = 0
                                self.mouse_mode        = False
                                action_label = "LEFT CLICK!"
                                print("  -> Index Click")
                        else:
                            action_label = "..."

                    else:
                        if self.mouse_mode:
                            self.mouse_exit_frames += 1
                            if self.mouse_exit_frames > MOUSE_EXIT_TOLERANCE:
                                self.mouse_mode        = False
                                self.mouse_exit_frames = 0
                                self.index_frame_cnt   = 0
                                self.trail.clear()
                        else:
                            self.mouse_exit_frames = 0
                            self.index_frame_cnt   = 0
                            al = self._process_normal(cx, cy, tx, ty, now, lm)
                            if al: action_label = al

                if self.show_window:
                    for hand_lm in result.multi_hand_landmarks:
                        pass  # landmark dots kaldirildi (v2.0)

            else:
                if self.active:
                    if self.alttab_mode:
                        keyboard.release("alt")
                        self.alttab_mode      = False
                        self.two_hand_still_t = None
                        print("  -> Alt released (el kayboldu)")
                    self.palm_buf.clear()
                    self.two_hand_buf.clear()
                    self._reset_zone_state()
                    self.tap_frames.clear()
                    self.tap_triggered.clear()
                    self.scr_hold = 0; self.vol_hold = 0
                    self.mouse_mode = False
                    self.index_frame_cnt = 0; self.mouse_exit_frames = 0
                    if self.scr_locked and self.scr_countdown is None:
                        self.scr_countdown = now + LOCK_COUNTDOWN
                    if self.vol_locked and self.vol_countdown is None:
                        self.vol_countdown = now + LOCK_COUNTDOWN

            if action_label: last_label = action_label

            if self.show_window:
                self._draw_hud(frame, action_label, last_label, now)
                cv2.imshow("VISION App", frame)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord('v'), ord('V')): self.toggle()
                elif key in (ord('q'), ord('Q')):
                    self._running = False; break

        self.cap.release()
        cv2.destroyAllWindows()

    def _process_normal(self, cx, cy, tx, ty, now, lm) -> str:
        action_label = ""
        self.gesture_trail.append((cx, cy, now))

        # Alt+Tab modu aktifken tum butonlar deaktif, sadece swipe calisir
        if self.alttab_mode:
            self.ok_hold_start = None
            self.palm_buf.append((cx, cy, now))
            yon, _ = calc_swipe(self.palm_buf, SWIPE_THRESHOLD)
            if yon == "left":
                self.palm_buf.clear()
                self.last_trigger[Gesture.SWIPE_LEFT] = 0
                self._execute_gesture(Gesture.SWIPE_LEFT)
                action_label = "ALT+TAB: PREV"
            elif yon == "right":
                self.palm_buf.clear()
                self.last_trigger[Gesture.SWIPE_RIGHT] = 0
                self._execute_gesture(Gesture.SWIPE_RIGHT)
                action_label = "ALT+TAB: NEXT"
            else:
                action_label = "ALT+TAB: ACTIVE"
            return action_label

        # ─── OK isareti - Alt+Tab ac (1sn hold) ────────────────────────────
        ok = is_ok(lm)
        if ok:
            if self.ok_hold_start is None:
                self.ok_hold_start = now
            elapsed = now - self.ok_hold_start
            action_label = f"OK {elapsed:.1f}s / {OK_HOLD_SECS}s"
            if elapsed >= OK_HOLD_SECS:
                self.ok_hold_start = None
                keyboard.press("alt")
                keyboard.press_and_release("tab")
                self.alttab_mode   = True
                self.last_action_t = now
                print("  -> Alt+Tab opened (OK gesture)")
                action_label = "ALT+TAB!"
            return action_label
        else:
            self.ok_hold_start = None

        # Scroll lock aktif - gesture'lar inaktif
        if self.scr_locked:
            if self.scr_countdown is None:
                self.scr_countdown = now + LOCK_COUNTDOWN
            action_label = self._do_scroll(cy)
            self._reset_zone_state()
            self.palm_buf.clear()
            self.ok_hold_start = None
            return action_label

        # Vol lock aktif - gesture'lar inaktif
        if self.vol_locked:
            if self.vol_countdown is None:
                self.vol_countdown = now + LOCK_COUNTDOWN
            action_label = self._do_volume(cy)
            self._reset_zone_state()
            self.palm_buf.clear()
            self.ok_hold_start = None
            return action_label

        # Tap zone kontrolu - isaret parmagi ucu ile
        tap_zone = get_tap_zone(tx, ty)
        TAP_ZONES = {Zone.TOP_C1, Zone.TOP_C2, Zone.TOP_C3, Zone.TOP_C4}

        if tap_zone in TAP_ZONES:
            self.palm_buf.clear()
            self._reset_zone_state()
            self.scr_hold = 0; self.vol_hold = 0
            self.tap_frames[tap_zone] = self.tap_frames.get(tap_zone, 0) + 1
            for z in TAP_ZONES:
                if z != tap_zone:
                    self.tap_frames[z] = 0
                    self.tap_triggered.discard(z)
            if self.tap_frames[tap_zone] >= TAP_FRAMES and tap_zone not in self.tap_triggered:
                self.tap_triggered.add(tap_zone)
                self._execute_zone(tap_zone)
                action_label = tap_zone.name
            return action_label

        # Tap zone disindaysa tap sayaclarini sifirla
        for z in TAP_ZONES:
            self.tap_frames[z] = 0
            self.tap_triggered.discard(z)

        # Palm zone - lock butonlari ve media
        palm_zone = get_palm_zone(cx, cy)

        if palm_zone == Zone.SCR_BTN:
            self.scr_hold += 1
            self.palm_buf.clear()
            self._reset_zone_state()
            if self.scr_hold >= 5:
                self.scr_locked    = True
                self.scr_countdown = now + LOCK_COUNTDOWN
                self.scr_last_y    = cy
                self.scr_hold      = 0
                self.lock_engaged_t= now
                action_label       = "SCROLL LOCKED"
                print("  -> Scroll locked")

        elif palm_zone == Zone.VOL_BTN:
            self.vol_hold += 1
            self.palm_buf.clear()
            self._reset_zone_state()
            if self.vol_hold >= 5:
                self.vol_locked    = True
                self.vol_countdown = now + LOCK_COUNTDOWN
                self.vol_last_y    = cy
                self.vol_hold      = 0
                self.lock_engaged_t= now
                action_label       = "VOL LOCKED"
                print("  -> Vol locked")

        elif palm_zone in (Zone.BOT_C1, Zone.BOT_C2, Zone.BOT_C3):
            self.scr_hold = 0; self.vol_hold = 0
            self.palm_buf.clear()
            if palm_zone == self.current_zone:
                self.zone_hold_count += 1
            else:
                self.current_zone    = palm_zone
                self.zone_hold_count = 1
                self.zone_triggered  = False
            if self.zone_hold_count >= ZONE_HOLD_FRAMES and not self.zone_triggered:
                self.zone_triggered = True
                self._execute_zone(palm_zone)
                action_label = palm_zone.name

        else:
            self.scr_hold = 0; self.vol_hold = 0
            self._reset_zone_state()
            self.palm_buf.append((cx, cy, now))
            yon, _ = calc_swipe(self.palm_buf, SWIPE_THRESHOLD)
            if yon == "left":
                self.palm_buf.clear()
                # alttab_mode'da cooldown'i dusur
                if self.alttab_mode:
                    self.last_trigger[Gesture.SWIPE_LEFT] = 0
                self._execute_gesture(Gesture.SWIPE_LEFT)
                action_label = "SWIPE LEFT"
            elif yon == "right":
                self.palm_buf.clear()
                if self.alttab_mode:
                    self.last_trigger[Gesture.SWIPE_RIGHT] = 0
                self._execute_gesture(Gesture.SWIPE_RIGHT)
                action_label = "SWIPE RIGHT"

        return action_label

    def _do_scroll(self, cy: float) -> str:
        if self.scr_last_y is None:
            self.scr_last_y = cy; return "SCROLL ACTIVE"
        delta = cy - self.scr_last_y
        self.scr_last_y = cy
        if abs(delta) < SCROLL_MOVE_THRESH: return "SCROLL ACTIVE"
        amount = int(min(SCROLL_MAX, max(SCROLL_MIN, abs(delta) * SCROLL_SPEED_MULT)))
        # el yukari (delta < 0) = scroll up, el asagi (delta > 0) = scroll down
        if delta < 0:
            pyautogui.scroll(amount)
            return "SCROLL UP"
        else:
            pyautogui.scroll(-amount)
            return "SCROLL DOWN"

    def _do_volume(self, cy: float) -> str:
        if self.vol_last_y is None:
            self.vol_last_y = cy; return "VOL ACTIVE"
        delta = cy - self.vol_last_y
        self.vol_last_y = cy
        if abs(delta) < VOL_MOVE_THRESH: return "VOL ACTIVE"
        # Dinamik hiz bazli - scroll ile ayni mantik
        presses = int(min(VOL_MAX, max(VOL_MIN, abs(delta) * VOL_MULT)))
        if delta < 0:
            for _ in range(presses): keyboard.press_and_release("volume up")
            return "VOL UP"
        else:
            for _ in range(presses): keyboard.press_and_release("volume down")
            return "VOL DOWN"

    def _check_two_hand(self, lm1, lm2):
        cx1, cy1 = palm_center(lm1)
        cx2, cy2 = palm_center(lm2)
        mx, my = (cx1+cx2)/2, (cy1+cy2)/2
        now = time.time()

        # ─── Swipe tespiti (her zaman calisir) ─────────────────────────────
        self.two_hand_buf.append((mx, my, now))
        swipe_gesture = Gesture.NONE
        if len(self.two_hand_buf) >= 8:
            pts = list(self.two_hand_buf)
            dx = pts[-1][0] - pts[0][0]
            dy = pts[-1][1] - pts[0][1]
            if abs(dy) > abs(dx) and abs(dy) > TWO_HAND_THRESHOLD and dy > 0:
                self.two_hand_buf.clear()
                self.two_hand_still_t = None
                swipe_gesture = Gesture.TWO_HAND_DOWN

        if swipe_gesture != Gesture.NONE:
            return swipe_gesture, 0.0

        # ─── Duragan tespiti (sadece alttab_mode'da, kapat) ────────────────
        if self.alttab_mode:
            if self.two_hand_last_pos is not None:
                dx = abs(mx - self.two_hand_last_pos[0])
                dy = abs(my - self.two_hand_last_pos[1])
                if dx < TWO_HAND_STILL_THRESH and dy < TWO_HAND_STILL_THRESH:
                    if self.two_hand_still_t is None:
                        self.two_hand_still_t = now
                    elif now - self.two_hand_still_t >= TWO_HAND_STILL_SECS:
                        keyboard.release("alt")
                        self.alttab_mode      = False
                        self.two_hand_still_t = None
                        self.two_hand_last_pos= None
                        self.two_hand_buf.clear()
                        print("  -> Alt released (still)")
                        return Gesture.NONE, 0.0
                else:
                    self.two_hand_still_t = None
            self.two_hand_last_pos = (mx, my)

        return Gesture.NONE, 0.0

    def _execute_zone(self, zone):
        now = time.time()
        if now - self.last_trigger.get(zone, 0) < COOLDOWN: return
        self.last_trigger[zone] = now
        threading.Thread(target=self._do_zone, args=(zone,), daemon=True).start()

    def _do_zone(self, zone):
        if zone == Zone.TOP_C1:
            pyautogui.hotkey("ctrl", "t"); print("  -> Open Tab")
        elif zone == Zone.TOP_C2:
            pyautogui.hotkey("ctrl", "shift", "t"); print("  -> Reopen Tab")
        elif zone == Zone.TOP_C3:
            pyautogui.hotkey("ctrl", "tab"); print("  -> Next Tab")
        elif zone == Zone.TOP_C4:
            pyautogui.hotkey("ctrl", "w"); print("  -> Close Tab")
        elif zone == Zone.BOT_C1:
            try: keyboard.press_and_release("previous track")
            except: pyautogui.press("prevtrack")
        elif zone == Zone.BOT_C2:
            keyboard.press_and_release("play/pause media")
        elif zone == Zone.BOT_C3:
            try: keyboard.press_and_release("next track")
            except: pyautogui.press("nexttrack")

    def _execute_gesture(self, gesture, value=0.0):
        now = time.time()
        # Lock sonrasi 2sn gesture inaktif
        if now - self.lock_engaged_t < LOCK_GESTURE_BLOCK:
            remaining = LOCK_GESTURE_BLOCK - (now - self.lock_engaged_t)
            print(f"  -> Gesture blocked after lock ({remaining:.1f}s remaining)")
            return
        # Two-hand down koruma: son aksiyondan 5sn gectiyse calis
        if gesture == Gesture.TWO_HAND_DOWN:
            if now - self.last_action_t < TWO_HAND_DOWN_LOCK:
                remaining = TWO_HAND_DOWN_LOCK - (now - self.last_action_t)
                print(f"  -> TWO_HAND_DOWN blocked ({remaining:.1f}s remaining)")
                return
        if now - self.last_trigger.get(gesture, 0) < COOLDOWN: return
        self.last_trigger[gesture] = now
        threading.Thread(target=self._do_gesture, args=(gesture, value), daemon=True).start()

    def _do_gesture(self, gesture, value=0.0):
        if gesture == Gesture.SWIPE_LEFT:
            if self.alttab_mode:
                keyboard.press_and_release("shift+tab")
                print("  -> Alt+Tab: prev window")
            else:
                pyautogui.hotkey("alt", "left")
            self.last_action_t = time.time()
        elif gesture == Gesture.SWIPE_RIGHT:
            if self.alttab_mode:
                keyboard.press_and_release("tab")
                print("  -> Alt+Tab: next window")
            else:
                pyautogui.hotkey("alt", "right")
            self.last_action_t = time.time()
        elif gesture == Gesture.TWO_HAND_DOWN:
            pyautogui.hotkey("alt", "f4")
            self.last_action_t = time.time()

    def _draw_hud(self, frame, action_label, last_label, now):
        h, w = frame.shape[:2]
        overlay = frame.copy()

        if not self.active:
            cv2.putText(frame, "VISION App: STANDBY", (8, h-8),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.50, (0,100,200), 1)
            return

        # Mouse Trail (v2.0)
        if len(self.trail) > 1:
            tl = list(self.trail)
            for i in range(1, len(tl)):
                x1, y1, t1, s1 = tl[i-1]
                x2, y2, t2, s2 = tl[i]
                age = now - t2
                if age > TRAIL_FADE_S: continue
                a   = 1.0 - age / TRAIL_FADE_S
                sf  = min(1.0, s2 * 3.0)
                thk = max(1, int((2 + 4*sf) * a))
                rc  = int(100 + 155*sf*a); gc = int(180*a); bc = int(255*a)
                cv2.line(frame,
                         (int(x1*w), int(y1*h)),
                         (int(x2*w), int(y2*h)),
                         (bc, gc, rc), thk)
                if sf > 0.5 and a > 0.5:
                    cv2.circle(frame, (int(x2*w), int(y2*h)),
                               max(1, int(3*sf*a)), (bc, gc, rc), -1)

        # Zone Glow
        if len(self.gesture_trail) > 0 and not self.mouse_mode:
            lcx, lcy, _ = self.gesture_trail[-1]
            for bx,by in [(BTN_TOP_C1_X,BTN_TOP_C1_Y),(BTN_TOP_C2_X,BTN_TOP_C2_Y),
                          (BTN_TOP_C3_X,BTN_TOP_C3_Y),(BTN_TOP_C4_X,BTN_TOP_C4_Y),
                          (VOL_BTN_X,VOL_BTN_Y),(SCR_BTN_X,SCR_BTN_Y),
                          (BTN_BOT_C1_X,BTN_BOT_C1_Y),(BTN_BOT_C2_X,BTN_BOT_C2_Y),
                          (BTN_BOT_C3_X,BTN_BOT_C3_Y)]:
                d = dist_2d(lcx, lcy, bx, by)
                if d < GLOW_RADIUS:
                    ga = (1.0 - d/GLOW_RADIUS) * 0.35
                    px, py = int(bx*w), int(by*h)
                    go = frame.copy()
                    cv2.circle(go, (px,py), int(BTN_RECT_W*w*0.8), (255,200,50), -1)
                    cv2.addWeighted(go, ga, frame, 1-ga, 0, frame)

        CIRCLE_ONLY = {Zone.BOT_C2}
        TAP_ZONES   = {Zone.TOP_C1, Zone.TOP_C2, Zone.TOP_C3, Zone.TOP_C4}

        def draw_btn(bx, by, label, zone_enum, is_lock=False, lock_col=None, is_tap=False):
            px, py = int(bx*w), int(by*h)
            mdim   = self.mouse_mode

            is_hov   = (self.current_zone == zone_enum and self.zone_hold_count > 0 and not self.zone_triggered)
            progress = (self.zone_hold_count/ZONE_HOLD_FRAMES) if is_hov else 0
            tap_p    = min(1.0, self.tap_frames.get(zone_enum,0)/TAP_FRAMES) if is_tap else 0.0
            if is_tap and tap_p > 0: is_hov = True

            locked, hold_p, countdown_rem = False, 0.0, None
            if zone_enum == Zone.SCR_BTN:
                locked=self.scr_locked; hold_p=min(1.0,self.scr_hold/5.0)
                lock_col=lock_col or (0,220,80)
                if self.scr_countdown is not None: countdown_rem=max(0,self.scr_countdown-now)
            elif zone_enum == Zone.VOL_BTN:
                locked=self.vol_locked; hold_p=min(1.0,self.vol_hold/5.0)
                lock_col=lock_col or (255,140,0)
                if self.vol_countdown is not None: countdown_rem=max(0,self.vol_countdown-now)

            alpha      = 0.10 if mdim else BTN_ALPHA
            use_circle = is_lock or (zone_enum in CIRCLE_ONLY)
            r  = int(SCR_BTN_R*min(w,h)) if is_lock else int(BTN_CIRCLE_R*min(w,h))
            rw = int(BTN_RECT_W*w); rh = int(BTN_RECT_H*h)

            # Renk hesapla
            if is_lock and locked:
                lc = lock_col or (0,180,60)
                border = lc
                fill   = (border[0]//4, border[1]//4, border[2]//4)
            elif is_hov or (is_lock and hold_p > 0):
                p = tap_p if is_tap else (progress if is_hov else hold_p)
                fill   = (int(160*p), int(60*p), int(10*p))
                border = (255, 220, 50)
            else:
                fill   = (20, 12, 5)
                border = (50, 50, 50) if mdim else (180, 100, 0)

            thickness = 2 if (is_hov or (is_lock and locked)) else 1

            # ─── Buton gövdesi ──────────────────────────────────────────────
            if use_circle:
                cv2.circle(frame, (px,py), r, (8, 5, 2), -1); overlay[:] = frame[:]
                cv2.circle(overlay, (px,py), r, fill, -1)
                cv2.addWeighted(overlay, alpha, frame, 1-alpha, 0, frame); overlay[:] = frame[:]
                cv2.circle(frame, (px,py), r, border, thickness)
            else:
                x1,y1 = px-rw//2, py-rh//2; x2,y2 = px+rw//2, py+rh//2
                cv2.rectangle(frame, (x1,y1), (x2,y2), (8, 5, 2), -1); overlay[:] = frame[:]
                cv2.rectangle(overlay, (x1,y1), (x2,y2), fill, -1)
                cv2.addWeighted(overlay, alpha, frame, 1-alpha, 0, frame); overlay[:] = frame[:]
                cv2.rectangle(frame, (x1,y1), (x2,y2), border, thickness)

            # ─── Efektler ────────────────────────────────────────────────────
            NEON     = (0, 215, 255)   # altın neon (BGR)
            NEON2    = (0, 180, 255)   # ikinci katman
            ARC_SPD  = 180             # derece/saniye (yuvarlak, eski hiz)
            ARC_LEN  = 90             # arc uzunlugu (derece)

            active_p = 0.0
            if is_lock and locked:
                active_p = 1.0
            elif is_lock and hold_p > 0:
                active_p = hold_p
            elif is_hov:
                active_p = tap_p if is_tap else progress

            if active_p > 0 and not mdim:
                if use_circle:
                    # ─── Yuvarlak: dönen neon arc (eski) ───────────────────
                    spin_angle = (now * ARC_SPD) % 360
                    arc_extent = int(ARC_LEN * active_p) if active_p < 1.0 else ARC_LEN
                    ar = r + 4
                    axes = (ar, ar)
                    cv2.ellipse(frame, (px,py), axes, spin_angle, 0, arc_extent,
                                (0, 120, 180), 4)
                    cv2.ellipse(frame, (px,py), axes, spin_angle, 0, arc_extent,
                                NEON, 2)
                    import math as _math
                    end_rad = _math.radians(spin_angle + arc_extent)
                    ex = int(px + ar * _math.cos(end_rad))
                    ey = int(py + ar * _math.sin(end_rad))
                    cv2.circle(frame, (ex, ey), 3, NEON2, -1)
                else:
                    # ─── Dikdörtgen: border parlaması (pulse) ──────────────
                    # active_p ile neon border kalınlığı artar
                    pulse_thk = max(1, int(active_p * 3))
                    pulse_col = (
                        int(NEON[0] * active_p),
                        int(NEON[1] * active_p),
                        int(NEON[2] * active_p)
                    )
                    cv2.rectangle(frame, (x1-1, y1-1), (x2+1, y2+1), pulse_col, pulse_thk)

            # ─── Etiket ─────────────────────────────────────────────────────
            if is_lock and locked and countdown_rem is not None:
                lbl = f"{countdown_rem:.1f}s"; lbl_color = NEON if active_p > 0 else border
            else:
                lbl = label; lbl_color = (35,35,35) if mdim else (160,65,0)
            cv2.putText(frame, lbl, (px-len(lbl)*5//2, py+5),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.28, lbl_color, 1)

        draw_btn(BTN_TOP_C1_X,BTN_TOP_C1_Y,"Open Tab",  Zone.TOP_C1,is_tap=True)
        draw_btn(BTN_TOP_C2_X,BTN_TOP_C2_Y,"Reopen Tab",Zone.TOP_C2,is_tap=True)
        draw_btn(BTN_TOP_C3_X,BTN_TOP_C3_Y,"Next Tab",  Zone.TOP_C3,is_tap=True)
        draw_btn(BTN_TOP_C4_X,BTN_TOP_C4_Y,"Close Tab", Zone.TOP_C4,is_tap=True)
        draw_btn(VOL_BTN_X,   VOL_BTN_Y,   "VOL",       Zone.VOL_BTN,is_lock=True)
        draw_btn(SCR_BTN_X,   SCR_BTN_Y,   "SCROLL",    Zone.SCR_BTN,is_lock=True)
        draw_btn(BTN_BOT_C1_X,BTN_BOT_C1_Y,"PREV",      Zone.BOT_C1)
        draw_btn(BTN_BOT_C2_X,BTN_BOT_C2_Y,"PLAY",      Zone.BOT_C2)
        draw_btn(BTN_BOT_C3_X,BTN_BOT_C3_Y,"NEXT",      Zone.BOT_C3)

        status_txt   = "ALT+TAB MODE" if self.alttab_mode else ("MOUSE MODE" if self.mouse_mode else "VISION App: ACTIVE")
        status_color = (0,100,255) if self.alttab_mode else ((0,200,255) if self.mouse_mode else (0,200,80))
        cv2.putText(frame,status_txt,(8,h-8),cv2.FONT_HERSHEY_SIMPLEX,0.48,status_color,1)
        if last_label:
            tw=len(last_label)*7
            cv2.putText(frame,last_label,(w-tw-45,h-8),cv2.FONT_HERSHEY_SIMPLEX,0.48,(0,180,255),1)
        cv2.putText(frame,"V=Toggle  Q=Quit",(w//2-55,h-4),cv2.FONT_HERSHEY_SIMPLEX,0.30,(40,40,40),1)


if __name__ == "__main__":
    vision = VisionController(camera_index=0, show_window=True)
    vision.active = True
    vision.start()
    try:
        while vision._running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        vision.stop()
