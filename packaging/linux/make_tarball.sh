#!/usr/bin/env bash
# Pack the Linux build:  packaging/linux/make_tarball.sh dist/Hadir 1.5.0 dist
# -> dist/Hadir-Linux-1.5.0.tar.gz holding Hadir-Linux-1.5.0/{Hadir/, install.sh, hadir.png, ...}
set -euo pipefail

build=${1:?the PyInstaller output folder (dist/Hadir)}
version=${2:?the version}
out=${3:?the output folder}
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
root=$(cd "$here/../.." && pwd)
name="Hadir-Linux-$version"
stage=$(mktemp -d)
trap 'rm -rf "$stage"' EXIT

mkdir -p "$stage/$name" "$out"
cp -a "$build" "$stage/$name/Hadir"
install -m 755 "$here/install.sh" "$stage/$name/install.sh"
install -m 644 "$here/README-ar.txt" "$stage/$name/اقرأني.txt"
install -m 644 "$root/hadir/resources/icons/hadir.png" "$stage/$name/hadir.png"
printf '%s\n' "$version" > "$stage/$name/VERSION"
tar -C "$stage" --owner=0 --group=0 --numeric-owner -czf "$out/$name.tar.gz" "$name"
ls -l "$out/$name.tar.gz"
