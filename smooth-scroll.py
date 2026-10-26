#!/usr/bin/env python3
"""Smooth scrolling for Linux guests in UTM/QEMU.

UTM passes scrolling from the Mac as whole wheel clicks (REL_WHEEL ±1 = 120 hi-res
units) through the spice vdagent tablet: the SPICE protocol cannot carry fractional
scrolling. The finger speed survives only in the RATE of the clicks (UTM accumulates
trackpad movement and emits one click per portion, including momentum).

The daemon grabs that device, passes everything except the wheel through to a clone,
and turns the wheel into small REL_WHEEL_HI_RES steps:
  * the rate is estimated over the last few clicks (UTM sends them in ~17 ms frames,
    4–5 per frame on a fast swipe); a gesture survives pauses up to GAP;
  * each click is spread evenly until the expected next one, so scrolling runs at a
    constant speed equal to the click rate, with no jump at the start;
  * the first click of a gesture glides over FIRST, a late remainder over ~50 ms.
Vertical scrolling is emitted through the "smooth scroll wheel" device in libinput's
"button scrolling" mode (see BUTTON_SCROLL), avoiding the 60-unit threshold of the
hi-res wheel path. Applications get smooth scrolling (XI2.1 smooth scrolling: GTK3,
Chromium/VSCode, Firefox, Qt5).

Settings are environment variables (see smooth-scroll.service):
  SMOOTH_SCALE    distance per click (1.0 = unchanged, 0.5 = half)
  SMOOTH_FIRST    how long the first click of a gesture glides, s
  SMOOTH_ACCEL    acceleration strength: gain = (rate/SMOOTH_RATE0)^ACCEL; 0 = off
  SMOOTH_RATE0    click rate (clicks/s) at which acceleration starts
  SMOOTH_MAXGAIN  acceleration limit
  SMOOTH_DEVICES  comma-separated names of source devices
  SMOOTH_CONFIG   JSON settings file (smooth-scroll-settings window); overrides the
                  variables above and is picked up live; {"enabled": false} releases the device
"""
import collections
import json
import math
import os
import select
import syslog
import threading
import time

from evdev import InputDevice, UInput, ecodes as e, list_devices

SCALE = float(os.environ.get("SMOOTH_SCALE", "0.08"))
FIRST = float(os.environ.get("SMOOTH_FIRST", "0.25"))  # how long the first click of a gesture glides, s
ACCEL = float(os.environ.get("SMOOTH_ACCEL", "0.8"))
RATE0 = float(os.environ.get("SMOOTH_RATE0", "6"))
MAXGAIN = float(os.environ.get("SMOOTH_MAXGAIN", "2"))
RAMP = float(os.environ.get("SMOOTH_RAMP", "0.3"))  # seconds of sustained rate before acceleration takes full effect
GAP = 0.6  # a pause between clicks longer than this starts a new gesture
MINSPAN = 1 / 60  # UTM sends clicks in ~17 ms frames
DELAY_K = 1.25  # buffer delay = DELAY_K × the longest of the recent intervals
DMIN = float(os.environ.get("SMOOTH_DMIN", "0.04"))  # delay on a fast swipe, s
DMAX = float(os.environ.get("SMOOTH_DMAX", "0.45"))  # delay limit when moving slowly, s
VSMOOTH = 0.02  # corner smoothing of the path, s
FRAME = 1 / 144
TARGETS = tuple(
    s.strip().lower()
    for s in os.environ.get("SMOOTH_DEVICES", "spice vdagent tablet").split(",")
    if s.strip()
)
CLONE_NAME = "smooth scroll tablet"
# Vertical scrolling goes through a separate device in libinput's "button scrolling" mode.
# The hi-res wheel path in libinput 1.28 accumulates 60 units (half a click) at the start of
# EVERY gesture and emits them at once — hence the long finger travel and the jump. In button
# mode the threshold only swallows 5 normalized units without emitting them; we spend those
# invisibly when pressing the button.
BUTTON_SCROLL = os.environ.get("SMOOTH_BUTTON_SCROLL", "1") != "0"
WHEEL_NAME = "smooth scroll wheel"  # the udev rule (MOUSE_DPI) and xorg.conf.d (ScrollMethod) match this name
SCROLL_BTN = e.BTN_TASK  # button 6+: libinput starts scrolling at once, without the 200 ms timer
PREROLL = 39  # threshold 5.0 normalized = 40 raw at 8000 dpi; one less, so the first real movement crosses it
DEBUG = os.environ.get("SMOOTH_DEBUG")  # file path: log raw clicks and output
CLICK = 120  # hi-res units per wheel click

# Settings file (written by the smooth-scroll-settings window); picked up live, without a restart.
CONFIG = os.environ.get("SMOOTH_CONFIG")
ENABLED = True
BASE = {  # values without a settings file — from the environment variables above
    "scale": SCALE, "accel": ACCEL, "rate0": RATE0, "maxgain": MAXGAIN, "ramp": RAMP,
    "first": FIRST, "dmin": DMIN, "dmax": DMAX, "smooth": 1.0,
}
LIMITS = {
    "scale": (0.01, 1.0), "accel": (0.0, 2.0), "rate0": (1.0, 60.0), "maxgain": (1.0, 5.0),
    "ramp": (0.0, 2.0), "first": (0.05, 1.0), "dmin": (0.01, 0.2), "dmax": (0.1, 1.0), "smooth": (0.25, 4.0),
}


def apply_config(cfg):
    """Sets parameters from a settings dict; "smooth" scales all glide times."""
    global ENABLED, SCALE, ACCEL, RATE0, MAXGAIN, RAMP, FIRST, DMIN, DMAX, VSMOOTH
    p = dict(BASE)
    for k, (lo, hi) in LIMITS.items():
        if isinstance(cfg.get(k), (int, float)):
            p[k] = min(max(float(cfg[k]), lo), hi)
    ENABLED = cfg.get("enabled", True) is not False
    SCALE, ACCEL, RATE0, MAXGAIN, RAMP = p["scale"], p["accel"], p["rate0"], p["maxgain"], p["ramp"]
    FIRST, DMIN, DMAX = p["first"] * p["smooth"], p["dmin"] * p["smooth"], p["dmax"] * p["smooth"]
    VSMOOTH = 0.02 * p["smooth"]


def watch_config():
    mtime = None
    while True:
        try:
            m = os.stat(CONFIG).st_mtime
            if m != mtime:
                mtime = m
                with open(CONFIG) as f:
                    apply_config(json.load(f))
                log(f"settings: enabled={ENABLED} scale={SCALE:.3f} accel={ACCEL:.2f} maxgain={MAXGAIN:.2f} first={FIRST:.2f}")
        except FileNotFoundError:
            if mtime is not None:
                mtime = None
                apply_config({})
        except (OSError, ValueError) as ex:
            log(f"settings file not read: {ex}")
        time.sleep(0.5)


def log(msg):
    print(msg, flush=True)
    syslog.syslog(syslog.LOG_INFO, f"smooth-scroll: {msg}")


def gain(rate):
    if ACCEL <= 0 or rate <= RATE0:
        return 1.0
    return min(MAXGAIN, (rate / RATE0) ** ACCEL)


class Axis:
    """One scroll axis: an interpolation buffer.

    Each click rebuilds the path: from the current position to the new target, evenly
    over the delay D, with VSMOOTH smoothing on top. D adapts to the click rate
    (DELAY_K × a recent interval): as long as D exceeds the interval, scrolling never stops.
    """

    def __init__(self, hires_code, legacy_code):
        self.hires_code = hires_code
        self.legacy_code = legacy_code
        self.reset()

    def reset(self):
        self.points = collections.deque()  # path points (τ, P), P is the accumulated distance
        self.target = 0.0  # P of the last point
        self.y = 0.0  # smoothed output
        self.sent = 0  # emitted distance, whole hi-res units
        self.legacy = 0  # accumulator for legacy REL_WHEEL clicks
        self.clicks = collections.deque(maxlen=6)  # times of the gesture's recent clicks
        self.start = 0.0  # time of the gesture's first click

    def ref(self, now):
        """Position on the path at time now."""
        while len(self.points) >= 2 and self.points[1][0] <= now:
            self.points.popleft()
        if not self.points:
            return self.target
        t0, p0 = self.points[0]
        if len(self.points) == 1 or now <= t0:
            return p0
        t1, p1 = self.points[1]
        return p0 + (p1 - p0) * (now - t0) / (t1 - t0)

    def push(self, hires_value, now):
        delta = hires_value * SCALE
        # Direction change: drop the rest of the old movement and stop where the output is now.
        if self.points and (self.target - self.y > 0) != (delta > 0) and abs(self.target - self.y) >= 1:
            self.points.clear()
            self.target = self.y
            self.clicks.clear()
        if self.clicks and now - self.clicks[-1] > GAP:
            self.clicks.clear()
        if not self.clicks:
            self.start = now  # gesture start
        self.clicks.append(now)
        if len(self.clicks) == 1:
            # First click of a gesture: no rate yet — start at once, unhurried, over FIRST.
            delay = FIRST
        else:
            iv = [b - a for a, b in zip(self.clicks, list(self.clicks)[1:])]
            # Over several recent intervals: UTM sends clicks in 17 ms frames, sometimes 4–5 per frame.
            delay = min(max(DELAY_K * max(iv[-3:]), DMIN), DMAX)
            span = self.clicks[-1] - self.clicks[0]
            # Acceleration ramps up only while a fast rate is sustained: the burst of clicks at the
            # start (macOS's kick at the beginning of a finger movement) must not give a big step at once.
            ramp = min(1.0, (now - self.start) / RAMP) if RAMP > 0 else 1.0
            g = gain((len(self.clicks) - 1) / span if span > 0 else 0.0)
            delta *= 1 + (g - 1) * ramp
        # The path is rebuilt from the current position to the new target over delay. At a steady
        # rate this is a constant speed equal to the rate; the headroom (delay > interval) prevents stops.
        here = self.ref(now)
        self.target += delta
        self.points = collections.deque([(now, here), (now + delay, self.target)])

    def active(self):
        return bool(self.points) or abs(self.target - self.y) >= 0.5 or round(self.y) != self.sent

    def step(self, dt, now):
        """Returns a list of (code, value) for this frame."""
        r = self.ref(now)
        if len(self.points) <= 1 and abs(r - self.y) < 1.0:
            self.y = r
            self.points.clear()
        else:
            self.y += (r - self.y) * (1 - math.exp(-dt / VSMOOTH))
        out = round(self.y) - self.sent
        self.sent += out
        events = []
        if out:
            events.append((self.hires_code, out))
            self.legacy += out
            if abs(self.legacy) >= CLICK:
                clicks = int(self.legacy / CLICK)
                self.legacy -= clicks * CLICK
                events.append((self.legacy_code, clicks))
        if not self.active():
            legacy = self.legacy
            self.reset()
            self.legacy = legacy
        return events


class ButtonScroll:
    """Emits vertical scrolling as movement with a held button (1 raw unit = 1/120 click)."""

    def __init__(self):
        self.ui = UInput({e.EV_KEY: [e.BTN_LEFT, SCROLL_BTN], e.EV_REL: [e.REL_X, e.REL_Y]}, name=WHEEL_NAME)
        self.held = False

    def scroll(self, hires_value):
        dy = -hires_value  # REL_Y down = scroll down; for the wheel +1 is up
        if not self.held:
            self.ui.write(e.EV_KEY, SCROLL_BTN, 1)
            self.ui.write(e.EV_REL, e.REL_Y, PREROLL if dy > 0 else -PREROLL)
            self.ui.syn()
            self.held = True
        self.ui.write(e.EV_REL, e.REL_Y, dy)
        self.ui.syn()

    def release(self):
        if self.held:
            self.ui.write(e.EV_KEY, SCROLL_BTN, 0)
            self.ui.syn()
            self.held = False

    def close(self):
        self.release()
        self.ui.close()


def run(src):
    caps = src.capabilities(absinfo=True)
    rel = set(caps.get(e.EV_REL, []))
    src_hires = {e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES} & rel

    clone_caps = {t: list(c) for t, c in caps.items() if t not in (e.EV_SYN, e.EV_FF)}
    clone_caps[e.EV_REL] = sorted(
        rel | {e.REL_WHEEL, e.REL_WHEEL_HI_RES, e.REL_HWHEEL, e.REL_HWHEEL_HI_RES}
    )
    ui = UInput(
        clone_caps,
        name=CLONE_NAME,
        vendor=src.info.vendor,
        product=src.info.product,
        version=src.info.version,
        bustype=src.info.bustype,
        input_props=src.input_props(),
    )
    src.grab()
    log(f"grabbed {src.name} ({src.path}), clone '{CLONE_NAME}', scale={SCALE} first={FIRST} accel={ACCEL}")

    axes = {
        "v": Axis(e.REL_WHEEL_HI_RES, e.REL_WHEEL),
        "h": Axis(e.REL_HWHEEL_HI_RES, e.REL_HWHEEL),
    }
    bs = ButtonScroll() if BUTTON_SCROLL else None
    mid_batch = False
    last = time.monotonic()
    dbg = open(DEBUG, "a", buffering=1) if DEBUG else None
    try:
        while True:
            animating = any(a.active() for a in axes.values())
            if not ENABLED and not animating:
                return  # turned off from the settings window — release the device (finally)
            r, _, _ = select.select([src.fd], [], [], FRAME if animating else 0.5)
            if not animating:
                last = time.monotonic()  # after idling, the first frame must not "catch up" on the pause
            if r:
                for ev in src.read():
                    if ev.type == e.EV_REL and ev.code in (
                        e.REL_WHEEL, e.REL_WHEEL_HI_RES, e.REL_HWHEEL, e.REL_HWHEEL_HI_RES
                    ):
                        vertical = ev.code in (e.REL_WHEEL, e.REL_WHEEL_HI_RES)
                        hires = ev.code in (e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES)
                        # If the source sends hi-res, legacy clicks are duplicates: use hi-res only.
                        wanted_hires = (e.REL_WHEEL_HI_RES if vertical else e.REL_HWHEEL_HI_RES) in src_hires
                        if hires != wanted_hires:
                            continue
                        now_in = time.monotonic()
                        if dbg:
                            dbg.write(f"{now_in:.4f} in {e.REL[ev.code]} {ev.value}\n")
                        axes["v" if vertical else "h"].push(ev.value if hires else ev.value * CLICK, now_in)
                    elif ev.type == e.EV_SYN:
                        ui.syn()
                        mid_batch = False
                    else:
                        ui.write(ev.type, ev.code, ev.value)
                        mid_batch = True
            now = time.monotonic()
            if not mid_batch and any(a.active() for a in axes.values()):
                dt = min(now - last, 0.05)
                wrote = False
                for a in axes.values():
                    for code, value in a.step(dt, now):
                        if dbg and code == e.REL_WHEEL_HI_RES:
                            dbg.write(f"{now:.4f} out {value}\n")
                        if bs and code == e.REL_WHEEL_HI_RES:
                            bs.scroll(value)
                        elif bs and code == e.REL_WHEEL:
                            pass  # X emulates legacy clicks itself from the smooth axis
                        else:
                            ui.write(e.EV_REL, code, value)
                            wrote = True
                if wrote:
                    ui.syn()
            if bs and not axes["v"].active():
                bs.release()
            last = now
    finally:
        try:
            src.ungrab()
        except Exception:
            pass
        ui.close()
        if bs:
            bs.close()
        log(f"released {src.name}")


def worker(path, busy):
    try:
        run(InputDevice(path))
    except OSError as ex:  # the device went away (vdagent recreates it when the resolution changes)
        log(f"{path}: {ex}")
    except Exception as ex:
        log(f"{path}: {type(ex).__name__}: {ex}")
    finally:
        busy.discard(path)


def main():
    log(f"start, sources: {TARGETS}, settings: {CONFIG}")
    if CONFIG:
        try:
            with open(CONFIG) as f:
                apply_config(json.load(f))
        except (OSError, ValueError):
            pass
        threading.Thread(target=watch_config, daemon=True).start()
    busy = set()
    while True:
        for path in list_devices() if ENABLED else ():
            if path in busy:
                continue
            try:
                dev = InputDevice(path)
                name = dev.name.lower()
                dev.close()
            except OSError:
                continue
            if name not in (CLONE_NAME, WHEEL_NAME) and any(t in name for t in TARGETS):
                busy.add(path)
                threading.Thread(target=worker, args=(path, busy), daemon=True).start()
        time.sleep(2)


if __name__ == "__main__":
    main()
