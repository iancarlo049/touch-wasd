#!/usr/bin/env python3
"""Multi-touch WASD overlay for Among Us (or any game) on Linux.

Reads the touchscreen directly so several fingers work at once:
  - A finger that lands in the BOTTOM-LEFT of the screen becomes the
    joystick (it appears under your finger) and sends W/A/S/D.
  - Every other finger becomes a normal mouse click/drag at that spot, so
    you can tap game buttons while walking.
Keys are sent through a virtual keyboard (/dev/uinput), so the game sees
normal key presses. Works on Wayland and X11.
"""
import math
import os
import queue
import signal
import sys
import threading

# Run Qt through XWayland: it lets us place a floating, see-through window anywhere.
os.environ.setdefault("QT_QPA_PLATFORM", "xcb")

try:
    import evdev
    from evdev import AbsInfo, UInput, ecodes as e
except ImportError:
    sys.exit("Missing python3-evdev. Run ./setup.sh first.")


class FastUInput(UInput):
    """UInput without the 2s wait trying to open its own /dev/input node (we never read it)."""

    def _find_device(self, fd):
        return None

try:
    from PyQt6.QtCore import QPointF, Qt, QTimer
    from PyQt6.QtGui import QColor, QFont, QPainter, QPen
    from PyQt6.QtWidgets import QApplication, QWidget
except ImportError:
    sys.exit("Missing PyQt6. Run ./setup.sh first.")

# ---------------- settings ----------------
JOYSTICK_SIZE = 260      # px, diameter of the joystick pad
DEADZONE = 0.25          # fraction of radius where nothing is pressed
# Joystick zone (bottom-left corner), as fractions of the screen:
LEFT_ZONE = 0.5          # how far across from the left edge
BOTTOM_ZONE = 0.5        # how far up from the bottom edge
OPACITY = 0.6            # how visible the joystick is while you use it (0..1)
IDLE_OPACITY = 0.25      # how visible it is while resting in the corner (0..1)
MARGIN = 40              # px from screen edges
TOUCH_DEVICE = None      # e.g. "/dev/input/event6"; None = auto-detect
# ------------------------------------------

ACCENT = (197, 17, 17)  # Among Us red

KEYCODES = {"W": e.KEY_W, "A": e.KEY_A, "S": e.KEY_S, "D": e.KEY_D}
WASD = {"W", "A", "S", "D"}

# 8 joystick sectors, counter-clockwise starting at "right"
SECTORS = [{"D"}, {"W", "D"}, {"W"}, {"W", "A"},
           {"A"}, {"S", "A"}, {"S"}, {"S", "D"}]


class Keyboard:
    """Virtual keyboard that tracks which keys it is holding."""

    def __init__(self):
        try:
            self.ui = FastUInput({e.EV_KEY: list(KEYCODES.values())}, name="touch-wasd")
        except PermissionError:
            sys.exit("No permission for /dev/uinput. Run ./setup.sh, then log out and back in.")
        self.held = set()

    def _send(self, key, value):
        self.ui.write(e.EV_KEY, KEYCODES[key], value)
        self.ui.syn()

    def press(self, key):
        if key not in self.held:
            self.held.add(key)
            self._send(key, 1)

    def release(self, key):
        if key in self.held:
            self.held.discard(key)
            self._send(key, 0)

    def close(self):
        for k in list(self.held):
            self.release(k)
        self.ui.close()


class Mouse:
    """Virtual absolute-position mouse for non-joystick fingers.

    The game only turns the *first* finger into a mouse, so a tap made while
    the joystick finger is down would be lost. We take over the touchscreen
    and click for it instead.
    """

    RANGE = 32767

    def __init__(self):
        axis = AbsInfo(value=0, min=0, max=self.RANGE, fuzz=0, flat=0, resolution=0)
        caps = {
            e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE],
            e.EV_ABS: [(e.ABS_X, axis), (e.ABS_Y, axis)],
        }
        self.ui = FastUInput(caps, name="touch-wasd pointer")
        self.pressed = False

    def move(self, fx, fy):
        self.ui.write(e.EV_ABS, e.ABS_X, round(min(max(fx, 0), 1) * self.RANGE))
        self.ui.write(e.EV_ABS, e.ABS_Y, round(min(max(fy, 0), 1) * self.RANGE))
        self.ui.syn()

    def button(self, down):
        if down != self.pressed:
            self.pressed = down
            self.ui.write(e.EV_KEY, e.BTN_LEFT, int(down))
            self.ui.syn()

    def close(self):
        self.button(False)
        self.ui.close()


# ---------------- touchscreen reading ----------------

def find_touchscreen():
    if TOUCH_DEVICE:
        return evdev.InputDevice(TOUCH_DEVICE)
    denied = False
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except PermissionError:
            denied = True
            continue
        abs_codes = dict(dev.capabilities().get(e.EV_ABS, []))
        direct = e.INPUT_PROP_DIRECT in dev.input_props()
        if direct and e.ABS_MT_POSITION_X in abs_codes and e.ABS_MT_SLOT in abs_codes:
            return dev
        dev.close()
    if denied:
        sys.exit("No permission to read the touchscreen. Run ./setup.sh, then log out and back in.")
    sys.exit("No multi-touch touchscreen found. Set TOUCH_DEVICE in overlay.py.")


def touch_reader(dev, out):
    """Background thread: turns raw multi-touch events into
    ('down'|'move'|'up', slot, fx, fy) with fx/fy as 0..1 screen fractions."""
    xi = dev.absinfo(e.ABS_MT_POSITION_X)
    yi = dev.absinfo(e.ABS_MT_POSITION_Y)
    slot = 0
    contacts = {}   # slot -> [x, y]
    changed = {}    # slot -> 'down' | 'move' | 'up' for this frame

    def mark(s, kind):
        if changed.get(s) != "down":  # down+move in one frame is still a down
            changed[s] = kind

    for ev in dev.read_loop():
        if ev.type == e.EV_ABS:
            if ev.code == e.ABS_MT_SLOT:
                slot = ev.value
            elif ev.code == e.ABS_MT_TRACKING_ID:
                if ev.value == -1:
                    if slot in contacts:
                        changed[slot] = "up"
                else:
                    contacts[slot] = [xi.min, yi.min]
                    changed[slot] = "down"
            elif ev.code == e.ABS_MT_POSITION_X and slot in contacts:
                contacts[slot][0] = ev.value
                mark(slot, "move")
            elif ev.code == e.ABS_MT_POSITION_Y and slot in contacts:
                contacts[slot][1] = ev.value
                mark(slot, "move")
        elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT:
            for s, kind in changed.items():
                x, y = contacts.get(s, (0, 0))
                fx = (x - xi.min) / max(1, xi.max - xi.min)
                fy = (y - yi.min) / max(1, yi.max - yi.min)
                out.put((kind, s, fx, fy))
                if kind == "up":
                    contacts.pop(s, None)
            changed.clear()


# ---------------- overlay window ----------------

def rgba(r, g, b, a):
    return QColor(r, g, b, round(max(0, min(1, a)) * 255))


class Joystick(QWidget):
    """See-through floating joystick that jumps to where the finger lands."""

    def __init__(self, kb, home):
        super().__init__()
        self.kb, self.home = kb, home
        self.size_px = JOYSTICK_SIZE
        self.r = self.size_px / 2 - 12
        self.c = self.size_px / 2
        self.knob_r = self.size_px * 0.14
        self.origin = None      # screen px where the finger landed
        self.knob = (0.0, 0.0)  # knob offset from centre
        self.keys = set()

        # Frameless, always on top, bypasses the window manager (never takes
        # focus), and clicks pass straight through it to the game.
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.X11BypassWindowManagerHint
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(self.size_px, self.size_px)
        self.move(*home)
        self.show()

    # --- input ---
    def start(self, x, y):
        self.origin = (x, y)
        half = self.size_px // 2
        self.move(int(x) - half, int(y) - half)
        self.drag(x, y)

    def drag(self, x, y):
        dx, dy = x - self.origin[0], y - self.origin[1]
        dist = math.hypot(dx, dy)
        self.knob = (dx, dy) if dist <= self.r else (dx * self.r / dist, dy * self.r / dist)
        if dist < self.r * DEADZONE:
            keys = set()
        else:
            angle = math.degrees(math.atan2(-dy, dx)) % 360
            keys = SECTORS[round(angle / 45) % 8]
        self._apply(keys)

    def stop(self):
        self.origin = None
        self.knob = (0.0, 0.0)
        self._apply(set())
        self.move(*self.home)

    def _apply(self, keys):
        for k in WASD - keys:
            self.kb.release(k)
        for k in keys:
            self.kb.press(k)
        self.keys = set(keys)
        self.update()

    # --- drawing ---
    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = OPACITY if self.origin else IDLE_OPACITY
        c, r = QPointF(self.c, self.c), self.r

        # pad
        p.setPen(QPen(rgba(255, 255, 255, a), 3))
        p.setBrush(rgba(13, 18, 23, 0.5 * a))
        p.drawEllipse(c, r, r)

        # dead zone
        pen = QPen(rgba(255, 255, 255, 0.5 * a), 1.5)
        pen.setStyle(Qt.PenStyle.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(c, r * DEADZONE, r * DEADZONE)

        # letters
        font = QFont("Sans", 18)
        font.setBold(True)
        p.setFont(font)
        off, box = r * 0.72, 40
        for key, (lx, ly) in {"W": (0, -off), "S": (0, off),
                              "A": (-off, 0), "D": (off, 0)}.items():
            if key in self.keys:
                p.setPen(rgba(*ACCENT, a + 0.3))
            else:
                p.setPen(rgba(255, 255, 255, a))
            p.drawText(round(self.c + lx - box / 2), round(self.c + ly - box / 2),
                       box, box, Qt.AlignmentFlag.AlignCenter, key)

        # knob
        p.setPen(QPen(rgba(255, 255, 255, a), 2))
        p.setBrush(rgba(255, 255, 255, 0.45 * a))
        p.drawEllipse(QPointF(self.c + self.knob[0], self.c + self.knob[1]),
                      self.knob_r, self.knob_r)
        p.end()


def main():
    dev = find_touchscreen()
    kb = Keyboard()
    mouse = Mouse()
    # Take the touchscreen for ourselves: we decide what is joystick and what is a click.
    try:
        dev.grab()
    except OSError:
        sys.exit("Something else has the touchscreen grabbed. Close other touch tools and retry.")
    events = queue.Queue()
    threading.Thread(target=touch_reader, args=(dev, events), daemon=True).start()

    app = QApplication(sys.argv)
    geo = app.primaryScreen().geometry()
    sw, sh = geo.width(), geo.height()

    # Joystick rests bottom-left and jumps to your finger on touch.
    stick = Joystick(kb, (MARGIN, sh - JOYSTICK_SIZE - MARGIN))

    stick_slot = None   # finger driving the joystick
    mouse_slot = None   # finger acting as the mouse

    def handle(kind, slot, fx, fy):
        nonlocal stick_slot, mouse_slot
        x, y = fx * sw, fy * sh
        if kind == "down":
            if stick_slot is None and fx < LEFT_ZONE and fy > 1 - BOTTOM_ZONE:
                stick_slot = slot
                stick.start(x, y)
            elif mouse_slot is None:
                mouse_slot = slot
                mouse.move(fx, fy)   # move first, then press, as separate events
                mouse.button(True)
        elif kind == "move":
            if slot == stick_slot:
                stick.drag(x, y)
            elif slot == mouse_slot:
                mouse.move(fx, fy)   # lets you drag (wires, card swipe, ...)
        elif kind == "up":
            if slot == stick_slot:
                stick_slot = None
                stick.stop()
            elif slot == mouse_slot:
                mouse_slot = None
                mouse.button(False)

    def poll():
        try:
            while True:
                handle(*events.get_nowait())
        except queue.Empty:
            pass

    timer = QTimer()
    timer.timeout.connect(poll)   # also gives Python a chance to see Ctrl+C
    timer.start(8)
    raiser = QTimer()
    raiser.timeout.connect(stick.raise_)
    raiser.start(2000)

    signal.signal(signal.SIGINT, lambda *_: app.quit())
    signal.signal(signal.SIGTERM, lambda *_: app.quit())

    print(f"touch-wasd running on {dev.name} ({dev.path}).", flush=True)
    print("Press Ctrl+C here to quit.", flush=True)
    try:
        app.exec()
    finally:
        kb.close()
        mouse.close()


if __name__ == "__main__":
    main()
