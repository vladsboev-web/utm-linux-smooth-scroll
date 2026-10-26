#!/bin/bash
# Install: bash install.sh (as your normal user; asks for the sudo password)
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
CONF="$HOME/.config/smooth-scroll/config.json"

# Dependencies: python3-evdev (daemon), python3-gi + GTK 3 (settings window), xinput.
missing=""
python3 -c "import evdev" 2>/dev/null || missing="$missing python3-evdev"
python3 -c "import gi; gi.require_version('Gtk', '3.0')" 2>/dev/null || missing="$missing python3-gi gir1.2-gtk-3.0"
command -v xinput >/dev/null || missing="$missing xinput"
if [ -n "$missing" ]; then
  echo "Installing dependencies:$missing"
  sudo apt-get install -y $missing
fi

# Old helpers, if present: imwheel breaks smooth scrolling.
pkill -x imwheel 2>/dev/null || true
[ -f ~/.config/autostart/imwheel.desktop ] && mv ~/.config/autostart/imwheel.desktop "$DIR/imwheel.desktop.bak"

# Natural (Mac-like) scrolling direction; XFCE stores it per device name.
if command -v xfconf-query >/dev/null; then
  for dev in smooth_scroll_tablet smooth_scroll_wheel; do
    xfconf-query -c pointers -p /$dev/ReverseScrolling -n -t bool -s true 2>/dev/null || \
    xfconf-query -c pointers -p /$dev/ReverseScrolling -s true
  done
fi

# Daemon, settings window, udev rule (scroll device DPI), X config ("button scrolling" mode).
sudo install -m 755 "$DIR/smooth-scroll.py" /usr/local/bin/smooth-scroll.py
sudo install -m 755 "$DIR/smooth-scroll-settings.py" /usr/local/bin/smooth-scroll-settings
sudo install -m 644 "$DIR/99-smooth-scroll.rules" /etc/udev/rules.d/99-smooth-scroll.rules
sudo install -D -m 644 "$DIR/50-smooth-scroll.conf" /etc/X11/xorg.conf.d/50-smooth-scroll.conf
sed "s#@CONFIG@#$CONF#" "$DIR/smooth-scroll.service" | sudo tee /etc/systemd/system/smooth-scroll.service > /dev/null
sudo udevadm control --reload
install -D -m 644 "$DIR/smooth-scroll-settings.desktop" ~/.local/share/applications/smooth-scroll-settings.desktop
install -D -m 644 "$DIR/smooth-scroll-tray.desktop" ~/.config/autostart/smooth-scroll-tray.desktop

sudo systemctl daemon-reload
sudo systemctl enable smooth-scroll.service
sudo systemctl restart smooth-scroll.service
# Restart the tray icon: otherwise a new instance would hand over to the old one.
pkill -x -u "$USER" -f "python3 /usr/local/bin/smooth-scroll-settings --tray" || true
sleep 1; (nohup /usr/local/bin/smooth-scroll-settings --tray >/dev/null 2>&1 &)
sleep 3
systemctl --no-pager is-active smooth-scroll.service
xinput list --name-only | grep -x "smooth scroll wheel" || echo "!! the scroll device did not appear — see: journalctl -u smooth-scroll"
