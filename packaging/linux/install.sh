#!/usr/bin/env bash
# حاضر — تثبيت البرنامج على لينكس لهذا المستخدم (لا يحتاج إلى كلمة مرور مدير النظام).
#
#   ./install.sh                  تثبيت، أو تحديث فوق إصدار سابق (البيانات تبقى كما هي)
#   ./install.sh --autostart      ومعه تشغيل شاشة الحضور تلقائياً عند الدخول إلى الجهاز
#   ./install.sh --no-autostart   إلغاء التشغيل التلقائي
#   ./install.sh --uninstall      إزالة البرنامج (البيانات تبقى كما هي)
#
# Hadir for Linux: installs for the current user into ~/.local/opt/hadir, with menu entries,
# a "hadir" command in ~/.local/bin and, if wanted, autostart of the attendance screen.
# The data stay in ~/.local/share/hadir and are never touched by this script.
set -euo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
PREFIX=${HADIR_PREFIX:-$HOME/.local/opt/hadir}
BIN_DIR=$HOME/.local/bin
APPS=$DATA_HOME/applications
ICON_DIR=$DATA_HOME/icons/hicolor/256x256/apps
AUTOSTART=$CONFIG_HOME/autostart/hadir.desktop
DATA=$DATA_HOME/hadir

say() { printf '%s\n' "$*"; }
fail() { printf '\nخطأ: %s\n' "$*" >&2; exit 1; }

case "$PREFIX" in
  */hadir) ;;
  *) fail "مجلد التثبيت يجب أن ينتهي بـ /hadir: $PREFIX" ;;
esac
case "$PREFIX" in
  *[\"\$\`\\]*) fail "مجلد التثبيت يحتوي على رموز غير مسموحة: $PREFIX" ;;
esac

action=install
autostart=ask
for arg in "$@"; do
  case "$arg" in
    --autostart) autostart=yes ;;
    --no-autostart) autostart=no ;;
    --uninstall) action=uninstall ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) fail "خيار غير معروف: $arg" ;;
  esac
done

stop_running() {
  # The program is replaced: close it first (the data are safe, every punch is already saved).
  local pattern="^$PREFIX/Hadir( |\$)"
  if pgrep -f "$pattern" > /dev/null 2>&1; then
    say "إغلاق حاضر المفتوح…"
    pkill -TERM -f "$pattern" || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      pgrep -f "$pattern" > /dev/null 2>&1 || return 0
      sleep 1
    done
    pkill -KILL -f "$pattern" || true
    sleep 1
  fi
}

desktop_entry() {  # file, English name, Arabic name, Arabic comment, arguments
  cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=$2
Name[ar]=$3
GenericName=Face attendance
GenericName[ar]=الحضور ببصمة الوجه
Comment[ar]=$4
Exec="$PREFIX/Hadir"${5:+ $5}
Icon=hadir
Terminal=false
Categories=Office;
StartupNotify=true
StartupWMClass=Hadir
EOF
}

set_autostart() {
  if [ "$autostart" = ask ]; then
    autostart=no
    if [ -f "$AUTOSTART" ]; then
      autostart=yes  # an update keeps the earlier choice
    elif [ -t 0 ]; then
      printf 'تشغيل شاشة الحضور تلقائياً عند الدخول إلى الجهاز؟ (موصى به لجهاز الباب) [ن/ل]: '
      read -r answer || answer=""
      case "$answer" in
        ن|نعم|y|Y|yes|Yes) autostart=yes ;;
      esac
    fi
  fi
  if [ "$autostart" = yes ]; then
    mkdir -p "$(dirname "$AUTOSTART")"
    desktop_entry "$AUTOSTART" "Hadir" "حاضر — شاشة الحضور" "تشغيل شاشة الحضور عند الدخول إلى الجهاز"
    printf 'X-GNOME-Autostart-enabled=true\nX-GNOME-Autostart-Delay=5\nX-KDE-autostart-after=panel\n' >> "$AUTOSTART"
    say "• تُفتح شاشة الحضور تلقائياً عند الدخول إلى الجهاز (لإلغاء ذلك: ./install.sh --no-autostart)"
  else
    rm -f "$AUTOSTART"
  fi
}

package_for() {  # a missing library -> the Arch / EndeavourOS package that has it
  case "$1" in
    libxcb-cursor.so*) echo xcb-util-cursor ;;
    libxcb-keysyms.so*) echo xcb-util-keysyms ;;
    libxcb-image.so*) echo xcb-util-image ;;
    libxcb-render-util.so*) echo xcb-util-renderutil ;;
    libxcb-icccm.so*) echo xcb-util-wm ;;
    libxcb-util.so*) echo xcb-util ;;
    libxkbcommon-x11.so*) echo libxkbcommon-x11 ;;
    libxkbcommon.so*) echo libxkbcommon ;;
    libxcb*.so*) echo libxcb ;;
    libX11*.so*) echo libx11 ;;
    libwayland-*.so*) echo wayland ;;
    libEGL.so*|libGL.so*|libOpenGL.so*|libGLX.so*) echo libglvnd ;;
    libfontconfig.so*) echo fontconfig ;;
    libfreetype.so*) echo freetype2 ;;
    libharfbuzz.so*) echo harfbuzz ;;
    libdbus-1.so*) echo dbus ;;
    libglib-2.0.so*|libgobject-2.0.so*|libgio-2.0.so*|libgthread-2.0.so*|libgmodule-2.0.so*) echo glib2 ;;
    libstdc++.so*|libgcc_s.so*) echo gcc-libs ;;
    libz.so*) echo zlib ;;
    libexpat.so*) echo expat ;;
    libpng16.so*) echo libpng ;;
    libjpeg.so*) echo libjpeg-turbo ;;
    libzstd.so*) echo zstd ;;
    *) echo "" ;;
  esac
}

check_system() {
  local qt="$PREFIX/_internal/PySide6/Qt"
  local needed=("$qt/lib/libQt6Core.so.6" "$qt/lib/libQt6Gui.so.6" "$qt/lib/libQt6Widgets.so.6"
                "$qt/lib/libQt6DBus.so.6" "$qt/lib/libQt6PrintSupport.so.6")
  local session=${XDG_SESSION_TYPE:-}
  if [ "$session" != x11 ]; then needed+=("$qt/plugins/platforms/libqwayland.so"); fi
  if [ "$session" != wayland ]; then needed+=("$qt/plugins/platforms/libqxcb.so"); fi
  local missing="" pkgs="" lib pkg file
  # The system's dynamic loader lists what each library needs, as ldd does (ldd itself is a
  # shell script: given the program's libraries it would run the shell with them).
  local loader=/lib64/ld-linux-x86-64.so.2
  if [ -x "$loader" ]; then
    for file in "${needed[@]}"; do
      [ -e "$file" ] || continue
      for lib in $(LD_TRACE_LOADED_OBJECTS=1 LD_LIBRARY_PATH="$PREFIX/_internal:$qt/lib" "$loader" "$file" 2>/dev/null \
                   | awk '/not found/ {print $1}'); do
        case " $missing " in *" $lib "*) continue ;; esac
        missing="$missing $lib"
        pkg=$(package_for "$lib")
        if [ -n "$pkg" ]; then
          case " $pkgs " in *" $pkg "*) ;; *) pkgs="$pkgs $pkg" ;; esac
        fi
      done
    done
  fi
  if [ -n "$missing" ]; then
    say ""
    say "⚠ مكتبات من النظام يحتاجها البرنامج ولم يُعثر عليها:$missing"
    if command -v pacman > /dev/null 2>&1 && [ -n "$pkgs" ]; then
      say "  ثبّتها بهذا الأمر (يطلب كلمة مرور المدير) ثم شغّل البرنامج:"
      say "    sudo pacman -S --needed$pkgs"
    else
      say "  ثبّتها بمدير الحزم في توزيعتك ثم شغّل البرنامج."
    fi
  fi
  if ! ls /dev/video* > /dev/null 2>&1; then
    say ""
    say "⚠ لم يُعثر على كاميرا (/dev/video*): وصّل الكاميرا أو فعّلها، ثم افتح البرنامج."
  else
    for file in /dev/video*; do
      if [ ! -r "$file" ] || [ ! -w "$file" ]; then
        say ""
        say "⚠ لا يملك هذا المستخدم صلاحية الكاميرا $file. نفّذ الأمر التالي ثم سجّل الخروج والدخول من جديد:"
        say "    sudo usermod -aG video $USER"
        break
      fi
    done
  fi
  if ! command -v pw-play > /dev/null 2>&1 && ! command -v paplay > /dev/null 2>&1 \
      && ! command -v aplay > /dev/null 2>&1; then
    say ""
    say "ملاحظة: لن تُسمع أصوات شاشة الحضور حتى يُثبَّت مشغّل صوت (pipewire أو alsa-utils)."
  fi
}

uninstall() {
  stop_running
  rm -rf "$PREFIX"
  if [ -f "$BIN_DIR/hadir" ] && grep -q "$PREFIX/Hadir" "$BIN_DIR/hadir"; then rm -f "$BIN_DIR/hadir"; fi
  rm -f "$APPS/hadir.desktop" "$APPS/hadir-admin.desktop" "$APPS/hadir-demo.desktop" "$AUTOSTART" \
        "$ICON_DIR/hadir.png"
  command -v update-desktop-database > /dev/null 2>&1 && update-desktop-database "$APPS" > /dev/null 2>&1 || true
  say "أُزيل حاضر. البيانات باقية في: $DATA"
  say "(لحذفها نهائياً احذف ذلك المجلد بنفسك بعد أخذ نسخة احتياطية إن احتجت إليها.)"
}

install() {
  [ "$(uname -m)" = x86_64 ] || fail "هذه النسخة لأجهزة 64 بت (x86_64) فقط."
  [ -x "$HERE/Hadir/Hadir" ] || fail "لم يُعثر على البرنامج بجانب هذا الملف: فك ضغط الملف كاملاً أولاً."
  say "تثبيت حاضر $(cat "$HERE/VERSION" 2>/dev/null || true) في $PREFIX …"
  stop_running
  mkdir -p "$(dirname "$PREFIX")"
  rm -rf "$PREFIX.new" "$PREFIX.old"
  cp -a "$HERE/Hadir" "$PREFIX.new"
  if [ -d "$PREFIX" ]; then mv "$PREFIX" "$PREFIX.old"; fi
  mv "$PREFIX.new" "$PREFIX"
  rm -rf "$PREFIX.old"

  mkdir -p "$BIN_DIR" "$APPS" "$ICON_DIR"
  printf '#!/bin/sh\nexec "%s/Hadir" "$@"\n' "$PREFIX" > "$BIN_DIR/hadir"
  chmod +x "$BIN_DIR/hadir"
  cp "$HERE/hadir.png" "$ICON_DIR/hadir.png"
  desktop_entry "$APPS/hadir.desktop" "Hadir" "حاضر — شاشة الحضور" "تسجيل حضور الموظفين وانصرافهم ببصمة الوجه"
  desktop_entry "$APPS/hadir-admin.desktop" "Hadir — Administration" "حاضر — الإدارة" \
    "إدارة الموظفين والدوام والتقارير" --admin
  desktop_entry "$APPS/hadir-demo.desktop" "Hadir — Demo" "حاضر — نسخة تجريبية ببيانات وهمية" \
    "تجربة البرنامج ببيانات منفصلة عن البيانات الحقيقية" --demo
  command -v update-desktop-database > /dev/null 2>&1 && update-desktop-database "$APPS" > /dev/null 2>&1 || true
  set_autostart

  say "✔ تم التثبيت."
  say "• شغّله من قائمة البرامج: «حاضر — شاشة الحضور» أو «حاضر — الإدارة»، أو بالأمر: $BIN_DIR/hadir"
  say "• البيانات (قاعدة البيانات وصور الأدلة والنسخ الاحتياطية): $DATA"
  check_system
}

if [ "$action" = uninstall ]; then uninstall; else install; fi
