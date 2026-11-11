#!/system/bin/sh
set -eu
kind=${1:-}
mode=${2:---check}
case "$kind" in arch) slot=kde20261109 ;; evox) slot=evox20261111 ;; *) printf '用法: sh enable-existing-dsu.sh arch|evox --check|--boot\n'; exit 2 ;; esac
case "$mode" in --check|--boot) ;; *) printf '只接受 --check 或 --boot\n'; exit 2 ;; esac
[ "$#" -le 2 ]
[ "$(id -u)" = 0 ] || { printf '需要在已有 root shell 中运行；脚本不会提权。\n'; exit 1; }
[ "$(getprop ro.product.device)" = socrates ]
[ "$(getprop ro.boot.slot_suffix)" = _a ]
[ "$(getprop ro.gsid.image_running)" = 0 ]
[ "$(getprop sys.boot_completed)" = 1 ]
target=/data/gsi/dsu/$slot
metadata=/metadata/gsi/dsu
[ -d "$target" ]
[ "$(cat "$metadata/$slot/install_dir")" = "$target/" ]
[ "$(cat "$metadata/$slot/complete")" = OK ]
command -v gsi_tool >/dev/null
printf '检查通过：socrates，硬件槽 a，现有 DSU %s。\n' "$slot"
if [ "$mode" = --check ]; then
    printf '当前仅检查；未启用、未重启、未写入镜像。\n'
    exit 0
fi
umask 077
backup=/data/local/tmp/k60pro-dsu-metadata-$(date +%Y%m%d-%H%M%S)
[ ! -e "$backup" ]
mkdir "$backup"
cp -a "$metadata" "$backup/dsu"
gsi_tool enable -s --dsuslot "$slot"
[ "$(cat "$metadata/active")" = "$slot" ]
printf '已备份元数据到 %s，准备单次启动 %s。\n' "$backup" "$slot"
sync
reboot
