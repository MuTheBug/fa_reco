#!/usr/bin/env bash
# CI check of the Linux build on Arch Linux (what EndeavourOS is built on), run in the
# archlinux container:  ci_arch_check.sh <version>   with /work holding the tar.gz and face.jpg.
# A desktop's libraries are installed, then a normal user installs the program with install.sh,
# runs its self-test, starts it on an X display, updates it and removes it.
set -euo pipefail
version=$1

pacman -Syu --noconfirm --needed fontconfig freetype2 libglvnd libxkbcommon libxkbcommon-x11 wayland dbus glib2 \
  xcb-util-cursor xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil ttf-dejavu ca-certificates \
  xorg-server-xvfb xorg-xauth procps-ng > /tmp/pacman.log 2>&1 || { tail -30 /tmp/pacman.log; exit 1; }
useradd -m tester
cp "/work/Hadir-Linux-$version.tar.gz" /work/face.jpg /home/tester/
chown tester: /home/tester/*

su - tester -s /bin/bash -c "set -euo pipefail
tar xzf Hadir-Linux-$version.tar.gz
cd Hadir-Linux-$version
./install.sh < /dev/null | tee install.log
if grep -q 'مكتبات من النظام' install.log; then echo 'libraries are missing on a desktop system'; exit 1; fi
test -x ~/.local/bin/hadir && test -f ~/.local/share/applications/hadir.desktop

echo '== self-test'
QT_QPA_PLATFORM=offscreen ~/.local/bin/hadir --selftest --selftest-image ~/face.jpg --selftest-out ~/selftest.txt \
  > ~/selftest.log 2>&1 || { cat ~/selftest.log; cat ~/selftest.txt 2>/dev/null; exit 1; }
cat ~/selftest.txt
grep -q 'certificate authorities from /etc/ssl/certs/ca-certificates.crt' ~/selftest.txt

echo '== the administration on an X display for 30 seconds (demo data, no camera)'
set +e
timeout 30 xvfb-run -a -s '-screen 0 1366x768x24' ~/.local/bin/hadir --demo --admin > ~/gui.log 2>&1
code=\$?
set -e
if [ \$code -ne 124 ]; then echo \"the program stopped early (exit \$code)\"; cat ~/gui.log; exit 1; fi
grep -v -i 'qt.qpa\|xdg_runtime\|propagateSizeHints' ~/gui.log | tail -20 || true
ls ~/.local/share/Hadir-Demo/demo.db

echo '== update over it, then remove'
./install.sh --autostart < /dev/null > /dev/null
test -f ~/.config/autostart/hadir.desktop
./install.sh --uninstall
test ! -e ~/.local/opt/hadir && test ! -e ~/.local/bin/hadir && test ! -e ~/.config/autostart/hadir.desktop
test -f ~/.local/share/Hadir-Demo/demo.db
echo 'ARCH CHECK OK'
"
