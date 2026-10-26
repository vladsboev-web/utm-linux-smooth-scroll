#!/bin/bash
# Complete removal
sudo systemctl disable --now smooth-scroll.service
sudo rm -f /etc/systemd/system/smooth-scroll.service /usr/local/bin/smooth-scroll.py /usr/local/bin/smooth-scroll-settings \
  /etc/udev/rules.d/99-smooth-scroll.rules /etc/X11/xorg.conf.d/50-smooth-scroll.conf
sudo rm -rf /etc/systemd/system/smooth-scroll.service.d
sudo systemctl daemon-reload
sudo udevadm control --reload
rm -f ~/.local/share/applications/smooth-scroll-settings.desktop ~/.config/autostart/smooth-scroll-tray.desktop
pkill -x -f "python3 /usr/local/bin/smooth-scroll-settings --tray" 2>/dev/null || true
[ -f "$(dirname "$0")/imwheel.desktop.bak" ] && mv "$(dirname "$0")/imwheel.desktop.bak" ~/.config/autostart/imwheel.desktop
echo "Removed. Your settings remain in ~/.config/smooth-scroll/ (delete manually if you like)."
