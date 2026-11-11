#!/usr/bin/env bash
# Fork-only Linux CI harness. No phone tools, credentials or real partitions.
set -euo pipefail
[[ ${GITHUB_ACTIONS:-} == true && $(id -u) == 0 ]]
[[ $(readlink /proc/self/ns/mnt) != $(readlink /proc/1/ns/mnt) ]]
binary=$(realpath -- "$1")
[[ -f $binary && -x $binary ]]
base=$(realpath -- "$RUNNER_TEMP")
top=$(mktemp -d "$base/skyward-disposable.XXXXXX")
jail=$top/jail
mkdir -p "$jail"/{dev,proc,system/bin,test,fixture/{source,target,backup}}
loops=()
cleanup() {
  set +e
  for at in "$jail/fixture/source" "$jail/fixture/target" "$jail/proc"; do
    if mountpoint -q "$at"; then umount "$at"; fi
  done
  for loop in "${loops[@]}"; do
    backing=$(losetup --noheadings --output BACK-FILE "$loop")
    case "$backing" in "$top/"*.img) losetup -d "$loop" ;; *) echo "Refusing to detach unowned loop $loop" >&2 ;; esac
  done
}
trap cleanup EXIT

copy_program() {
  local path actual dep destination
  path=$(command -v -- "$1")
  actual=$(realpath -- "$path")
  destination=${2:-$path}
  mkdir -p "$jail$(dirname "$destination")"
  cp "$actual" "$jail$destination"
  while read -r dep; do
    [[ -f $dep ]]
    mkdir -p "$jail$(dirname "$dep")"
    cp -L "$dep" "$jail$dep"
  done < <(ldd "$actual" | awk '/=> \/|^[[:space:]]*\// { for (i=1;i<=NF;i++) if ($i ~ /^\//) print $i }')
}
for program in mount umount blockdev cmp mke2fs e2fsck mkfs.f2fs fsck.f2fs; do
  copy_program "$program"
done
mkdir -p "$jail/bin" "$jail/sbin" "$jail/usr/bin" "$jail/etc"
cp /etc/mke2fs.conf "$jail/etc/"
ln -s "$(command -v mke2fs)" "$jail/system/bin/mke2fs"
ln -s "$(command -v e2fsck)" "$jail/system/bin/e2fsck"
ln -s "$(command -v mkfs.f2fs)" "$jail/system/bin/make_f2fs"
ln -s "$(command -v fsck.f2fs)" "$jail/system/bin/fsck.f2fs"
for program in mount umount; do
  actual=$(command -v "$program")
  [[ $actual == "/bin/$program" ]] || ln -s "$actual" "$jail/bin/$program"
done
actual=$(command -v blockdev)
[[ $actual == /sbin/blockdev ]] || ln -s "$actual" "$jail/sbin/blockdev"
actual=$(command -v cmp)
[[ $actual == /usr/bin/cmp ]] || ln -s "$actual" "$jail/usr/bin/cmp"
copy_program "$binary" /test/disposable
printf 'skyward-ci-loopback-v1\n' > "$jail/test/disposable-only"
for item in source:64 target:64 f2fs:128; do
  name=${item%:*}; mib=${item#*:}
  image=$top/$name.img
  truncate -s "${mib}M" "$image"
  loop=$(losetup --find --show --nooverlap "$image")
  loops+=("$loop")
  [[ $(realpath -- "$(losetup --noheadings --output BACK-FILE "$loop")") == "$image" ]]
  read -r major minor < <(stat -c '%t %T' "$loop")
  [[ $((16#$major)) == 7 ]]
  mknod "$jail/dev/test-$name" b "$((16#$major))" "$((16#$minor))"
  printf '%s -> disposable %s.img\n' "$name" "$name"
done
mknod "$jail/dev/null" c 1 3
mknod "$jail/dev/urandom" c 1 9
mknod "$jail/dev/random" c 1 8
chmod 0666 "$jail/dev/null" "$jail/dev/urandom" "$jail/dev/random"
mount -t proc proc "$jail/proc" -o nosuid,nodev,noexec
export LC_ALL=C
chroot "$jail" /test/disposable --ignored --exact disposable_loopbacks_round_trip --nocapture
