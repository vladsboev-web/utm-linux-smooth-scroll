#!/usr/bin/env python3
"""Settings window and tray icon for the smooth-scroll daemon.

Settings are written to ~/.config/smooth-scroll/config.json; the daemon picks them up live.
The tray icon (started with --tray) also keeps the X properties of the "smooth scroll wheel"
device: the daemon recreates it on restart, and without "button scrolling" it would move the cursor.
"""
import json
import os
import subprocess
import sys
import warnings

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

warnings.filterwarnings("ignore", category=DeprecationWarning)  # Gtk.StatusIcon is deprecated, but the XFCE tray only supports it

APP_ID = "app.smoothscroll.Settings"
CONFIG = os.path.join(GLib.get_user_config_dir(), "smooth-scroll", "config.json")
WHEEL_NAME = "smooth scroll wheel"
CLONE_NAME = "smooth scroll tablet"
SCROLL_BUTTON = "12"  # BTN_TASK in X button numbering
ICON = "input-mouse"
LANGS = [("en", "English"), ("ru", "Русский")]

DEFAULTS = {
    "enabled": True,
    "scale": 0.08, "accel": 0.8, "smooth": 1.0,
    "rate0": 6.0, "maxgain": 2.0, "ramp": 0.3, "first": 0.25, "dmin": 0.04, "dmax": 0.45,
    "natural": True,
}

# (key, min, max, step, digits)
SIMPLE = [
    ("scale", 0.02, 0.25, 0.01, 2),
    ("accel", 0.0, 1.5, 0.05, 2),
    ("smooth", 0.5, 2.0, 0.05, 2),
]
ADVANCED = [
    ("rate0", 2.0, 30.0, 0.5, 1),
    ("maxgain", 1.0, 4.0, 0.1, 1),
    ("ramp", 0.0, 1.0, 0.05, 2),
    ("first", 0.1, 0.6, 0.01, 2),
    ("dmin", 0.02, 0.12, 0.005, 3),
    ("dmax", 0.2, 0.8, 0.01, 2),
]

TEXT = {
    "en": {
        "title": "Smooth Scrolling",
        "language": "Language",
        "scale": ("Sensitivity", "Step when moving slowly: lower is finer and more precise"),
        "accel": ("Acceleration", "How much farther fast movements scroll; 0 means no acceleration"),
        "smooth": ("Smoothness", "Glide length: higher is softer but adds delay"),
        "rate0": ("Acceleration threshold, clicks/s", "Click rate at which acceleration starts"),
        "maxgain": ("Acceleration limit, ×", "Maximum step multiplier when scrolling fast"),
        "ramp": ("Acceleration ramp-up, s", "How long a fast rate must last before acceleration takes full effect"),
        "first": ("First click glide, s", "How long the first click of a gesture takes to glide"),
        "dmin": ("Delay when scrolling fast, s", "Buffer headroom for frequent clicks"),
        "dmax": ("Delay when moving slowly, s", "Buffer headroom limit: higher means fewer stops but feels heavier"),
        "advanced": "Advanced",
        "natural": "Natural scrolling (like on a Mac)",
        "reset": "Reset to defaults",
        "test": "Test — scroll here:",
        "test_line": "Line {i} — smooth scrolling test",
        "st_down": "The smooth-scroll service is not running ({st}): sudo systemctl start smooth-scroll",
        "st_off": "Off: scrolling works as without this program",
        "st_wait": "Service is running, waiting for the spice vdagent tablet device",
        "st_ok": "Running",
        "no_answer": "no answer",
        "menu_toggle": "Smooth scrolling",
        "menu_settings": "Settings…",
        "menu_quit": "Remove icon",
        "tip_on": "Smooth scrolling: on",
        "tip_off": "Smooth scrolling: off",
    },
    "ru": {
        "title": "Плавная прокрутка",
        "language": "Язык",
        "scale": ("Чувствительность", "Шаг при медленном ведении: меньше — мельче и точнее"),
        "accel": ("Ускорение", "Насколько быстрые движения прокручивают дальше; 0 — без ускорения"),
        "smooth": ("Плавность", "Длина доезда: больше — мягче, но с большей задержкой"),
        "rate0": ("Порог ускорения, щелчков/с", "С какого темпа щелчков включается ускорение"),
        "maxgain": ("Потолок ускорения, ×", "Во сколько раз максимум усиливается шаг при быстром листании"),
        "ramp": ("Нарастание ускорения, с", "Сколько должен держаться быстрый темп, чтобы ускорение вошло в силу"),
        "first": ("Доезд первого щелчка, с", "За сколько проезжает первый щелчок жеста"),
        "dmin": ("Задержка при быстром листании, с", "Запас буфера при частых щелчках"),
        "dmax": ("Задержка при медленном ведении, с", "Потолок запаса буфера: больше — меньше остановок, но вязче"),
        "advanced": "Дополнительно",
        "natural": "Естественное направление (как на маке)",
        "reset": "Сбросить к стандартным",
        "test": "Проверка — покрутите здесь:",
        "test_line": "Строка {i} — проверка плавной прокрутки",
        "st_down": "Служба smooth-scroll не работает ({st}): sudo systemctl start smooth-scroll",
        "st_off": "Выключено: прокрутка идёт как без программы",
        "st_wait": "Служба работает, ждёт устройство spice vdagent tablet",
        "st_ok": "Работает",
        "no_answer": "нет ответа",
        "menu_toggle": "Плавная прокрутка",
        "menu_settings": "Настройки…",
        "menu_quit": "Убрать значок",
        "tip_on": "Плавная прокрутка: включена",
        "tip_off": "Плавная прокрутка: выключена",
    },
}


def system_lang():
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        value = os.environ.get(var, "")
        if value:
            return "ru" if value.lower().startswith("ru") else "en"
    return "en"


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG) as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    if cfg.get("lang") not in TEXT:
        cfg["lang"] = system_lang()
    return cfg


def save_config(cfg):
    os.makedirs(os.path.dirname(CONFIG), exist_ok=True)
    tmp = CONFIG + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    os.replace(tmp, CONFIG)  # atomic: the daemon never reads a half-written file


def run(*args):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def daemon_status():
    return run("systemctl", "is-active", "smooth-scroll")


def wheel_device_id():
    return run("xinput", "list", "--id-only", WHEEL_NAME)


def apply_device_props(natural):
    """Button-scrolling mode and direction for the daemon's scroll device."""
    dev = wheel_device_id()
    if not dev:
        return False
    run("xinput", "set-prop", dev, "libinput Scroll Method Enabled", "0", "0", "1")
    run("xinput", "set-prop", dev, "libinput Button Scrolling Button", SCROLL_BUTTON)
    run("xinput", "set-prop", dev, "libinput Natural Scrolling Enabled", "1" if natural else "0")
    return True


def apply_natural(natural):
    # XFCE stores the direction per device name and applies it when the device appears.
    value = "true" if natural else "false"
    for name in (WHEEL_NAME, CLONE_NAME):
        key = "/" + name.replace(" ", "_") + "/ReverseScrolling"
        run("xfconf-query", "-c", "pointers", "-p", key, "-n", "-t", "bool", "-s", value)
        run("xfconf-query", "-c", "pointers", "-p", key, "-s", value)
    apply_device_props(natural)
    clone = run("xinput", "list", "--id-only", CLONE_NAME)
    if clone:
        run("xinput", "set-prop", clone, "libinput Natural Scrolling Enabled", "1" if natural else "0")


class SettingsWindow(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app)
        self.app = app
        self.set_default_size(520, 720)
        self.set_icon_name(ICON)
        self.save_id = 0
        self.outer = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.add(self.outer)
        self.build()
        GLib.timeout_add_seconds(2, self.refresh_status)

    def build(self, expanded=False):
        """Builds the content in the current language; called again when the language changes."""
        self.loading = True
        cfg = load_config()
        self.lang = cfg["lang"]
        t = TEXT[self.lang]
        self.set_title(t["title"])
        child = self.outer.get_child()
        if child:
            child.destroy()
        self.scales = {}

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin=16)
        self.outer.add(box)

        head = Gtk.Box(spacing=12)
        title = Gtk.Label(xalign=0)
        title.set_markup("<b>" + GLib.markup_escape_text(t["title"]) + "</b>")
        head.pack_start(title, True, True, 0)
        self.switch = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.switch.connect("notify::active", self.on_enabled)
        head.pack_end(self.switch, False, False, 0)
        self.lang_box = Gtk.ComboBoxText(valign=Gtk.Align.CENTER, tooltip_text=t["language"])
        for code, name in LANGS:
            self.lang_box.append(code, name)
        self.lang_box.connect("changed", self.on_lang)
        head.pack_end(self.lang_box, False, False, 0)
        box.pack_start(head, False, False, 0)
        self.status = Gtk.Label(xalign=0)
        self.status.get_style_context().add_class("dim-label")
        box.pack_start(self.status, False, False, 0)

        for spec in SIMPLE:
            box.pack_start(self.slider(spec), False, False, 0)

        self.expander = Gtk.Expander(label=t["advanced"], expanded=expanded)
        adv = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=8)
        for spec in ADVANCED:
            adv.pack_start(self.slider(spec), False, False, 0)
        nat = Gtk.Box(spacing=12)
        nat.pack_start(Gtk.Label(label=t["natural"], xalign=0), True, True, 0)
        self.natural = Gtk.Switch(valign=Gtk.Align.CENTER)
        self.natural.connect("notify::active", self.on_natural)
        nat.pack_end(self.natural, False, False, 0)
        adv.pack_start(nat, False, False, 0)
        self.expander.add(adv)
        box.pack_start(self.expander, False, False, 0)

        reset = Gtk.Button(label=t["reset"])
        reset.connect("clicked", self.on_reset)
        box.pack_start(reset, False, False, 0)

        box.pack_start(Gtk.Label(label=t["test"], xalign=0), False, False, 0)
        test = Gtk.ScrolledWindow(min_content_height=220)
        test.get_style_context().add_class("frame")
        view = Gtk.TextView(editable=False, cursor_visible=False, left_margin=8)
        view.get_buffer().set_text("\n".join(t["test_line"].format(i=i) for i in range(1, 501)))
        test.add(view)
        box.pack_start(test, True, True, 0)

        self.switch.set_active(bool(cfg["enabled"]))
        self.natural.set_active(bool(cfg["natural"]))
        self.lang_box.set_active_id(self.lang)
        for key, sc in self.scales.items():
            sc.set_value(float(cfg[key]))
        self.loading = False
        box.show_all()
        self.refresh_status()

    def slider(self, spec):
        key, lo, hi, step, digits = spec
        label, hint = TEXT[self.lang][key]
        col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        col.pack_start(Gtk.Label(label=label, xalign=0), False, False, 0)
        sc = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
        sc.set_digits(digits)
        sc.set_value_pos(Gtk.PositionType.RIGHT)
        sc.add_mark(DEFAULTS[key], Gtk.PositionType.BOTTOM, None)  # the mark shows the default value
        sc.connect("value-changed", self.on_changed)
        sc.connect("scroll-event", self.on_scale_scroll)
        col.pack_start(sc, False, False, 0)
        h = Gtk.Label(label=hint, xalign=0, wrap=True)
        h.get_style_context().add_class("dim-label")
        col.pack_start(h, False, False, 0)
        self.scales[key] = sc
        return col

    def on_scale_scroll(self, _scale, event):
        # The wheel over a slider scrolls the window instead of changing the value: change it with the mouse only.
        Gtk.propagate_event(self.outer, event)
        return True

    def load(self):
        self.build(self.expander.get_expanded())

    def collect(self):
        cfg = load_config()
        cfg.update({key: round(sc.get_value(), 4) for key, sc in self.scales.items()})
        cfg["enabled"] = self.switch.get_active()
        cfg["natural"] = self.natural.get_active()
        cfg["lang"] = self.lang
        return cfg

    def schedule_save(self):
        if self.loading:
            return
        if self.save_id:
            GLib.source_remove(self.save_id)
        self.save_id = GLib.timeout_add(150, self.do_save)  # don't write the file on every pixel of slider movement

    def do_save(self):
        self.save_id = 0
        save_config(self.collect())
        self.app.sync_tray()
        return False

    def on_changed(self, *_):
        self.schedule_save()

    def on_enabled(self, *_):
        self.schedule_save()
        GLib.timeout_add(1500, self.refresh_status)

    def on_natural(self, *_):
        if not self.loading:
            apply_natural(self.natural.get_active())
        self.schedule_save()

    def on_lang(self, combo):
        code = combo.get_active_id()
        if self.loading or not code or code == self.lang:
            return
        if self.save_id:
            GLib.source_remove(self.save_id)
            self.save_id = 0
        cfg = self.collect()
        cfg["lang"] = code
        save_config(cfg)
        self.app.sync_tray()
        GLib.idle_add(lambda: self.build(self.expander.get_expanded()) and False)  # rebuild outside the combo's own handler

    def on_reset(self, *_):
        self.loading = True
        for key, sc in self.scales.items():
            sc.set_value(DEFAULTS[key])
        self.loading = False
        self.schedule_save()

    def refresh_status(self):
        t = TEXT[self.lang]
        st = daemon_status()
        if st != "active":
            text = t["st_down"].format(st=st or t["no_answer"])
        elif not self.switch.get_active():
            text = t["st_off"]
        elif not wheel_device_id():
            text = t["st_wait"]
        else:
            text = t["st_ok"]
        self.status.set_text(text)
        return True


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.window = None
        self.tray = None
        self.last_dev = None

    def do_command_line(self, cmdline):
        if "--tray" in cmdline.get_arguments()[1:]:
            self.start_tray()
        else:
            self.show_window()
        return 0

    def show_window(self):
        if self.window is None:
            self.window = SettingsWindow(self)
            self.window.connect("destroy", self.on_window_destroy)
        self.window.show_all()
        self.window.present()

    def on_window_destroy(self, *_):
        self.window = None

    def start_tray(self):
        if self.tray:
            return
        self.hold()  # without a window the app stays alive for the icon
        self.tray = Gtk.StatusIcon.new_from_icon_name(ICON)
        self.sync_tray()
        self.tray.connect("activate", lambda *_: self.show_window())
        self.tray.connect("popup-menu", self.on_menu)
        self.check_device()
        GLib.timeout_add_seconds(2, self.check_device)

    def on_menu(self, icon, button, time):
        cfg = load_config()
        t = TEXT[cfg["lang"]]
        menu = Gtk.Menu()
        toggle = Gtk.CheckMenuItem(label=t["menu_toggle"])
        toggle.set_active(bool(cfg["enabled"]))
        toggle.connect("toggled", self.on_toggle)
        menu.append(toggle)
        item = Gtk.MenuItem(label=t["menu_settings"])
        item.connect("activate", lambda *_: self.show_window())
        menu.append(item)
        menu.append(Gtk.SeparatorMenuItem())
        item = Gtk.MenuItem(label=t["menu_quit"])
        item.connect("activate", lambda *_: self.quit())
        menu.append(item)
        menu.show_all()
        menu.popup(None, None, Gtk.StatusIcon.position_menu, icon, button, time)
        self.menu = menu  # keep a reference, otherwise the menu gets garbage-collected

    def on_toggle(self, item):
        cfg = load_config()
        cfg["enabled"] = item.get_active()
        save_config(cfg)
        self.sync_tray()
        if self.window:
            self.window.load()

    def sync_tray(self):
        if self.tray:
            cfg = load_config()
            self.tray.set_tooltip_text(TEXT[cfg["lang"]]["tip_on" if cfg["enabled"] else "tip_off"])

    def check_device(self):
        # The daemon recreates the device on restart — a new id gets the properties again.
        dev = wheel_device_id()
        if dev and dev != self.last_dev and apply_device_props(bool(load_config()["natural"])):
            self.last_dev = dev
        elif not dev:
            self.last_dev = None
        return True


if __name__ == "__main__":
    sys.exit(App().run(sys.argv))
