# K60 Pro：DSU 启动、保留数据与回退

本说明适用于 **REDMI K60 Pro，代号 socrates** 的本次个人验证包。不能用于 K90 Pro Max（myron）或其他机型。

## 版本与包的用途

- 机主确认当前移植系统的底包为 **OS3.0.303.0.WMKCNXM**。
- 当前宿主自报 Android 17／API 37、OS4.0.0.12.XMKCNXM；vendor/odm 属性自报 OS3.0.304.0.WMKCNXM、Android 13／API 33，VNDK 33。这些属性不等于整个底包的版本。
- recovery 的打包来源另为官方 Android 16 OTA OS3.0.304.0.WMKCNXM，属于构建输入；不据此改写机主确认的底包版本。
- EvoX、Arch 包保存已经验证过的 system 镜像，不打包个人 userdata，也不额外采集账户或无线密码。原镜像保留历史调试配置，尚未作为完全去个人化的公开发行版审计，因此按本人恢复包保存到草稿 Release。原数据卷和 DSU 元数据需要另行备份。
- 本包优先用于原测试设备、原 DSU 数据保留情况下的保存与恢复。更换底包、内核、数据卷或设备后，需要重新验证，不能只看 Android 大版本。

## 先分清两种槽位

| 名称 | 含义 | 本机已验证状态 |
| --- | --- | --- |
| Android 硬件槽 a / b | boot、vendor_boot 等 A/B 启动分区 | 本 K60 Pro 只在 a 操作；b 曾无法正常启动，禁止切到 b |
| DSU 名称 evox20261111 / kde20261109 | userdata 内不同的测试系统记录 | 分别为历史 EvoX、Arch 命名槽；不等于 Android 的硬件槽 b |

启动 DSU 不需要执行 `fastboot set_active`，也不需要刷 boot、init_boot、vendor_boot 或 vbmeta。不要把 DSU 镜像当作这些分区的刷机文件。

## 校验与准备

1. 用压缩包根目录的 `SHA256SUMS` 校验解压后的文件；镜像应在 `images/system.img`，recovery 应在 `images/recovery.img`。不要把不同包的镜像混用。
2. 在 HyperOS 中完成首次解锁，确认 USB 调试已授权、ADB 连接的是 socrates，并有可用的 root 环境。本轮已测环境依赖现有 KernelSU；不提供解锁 BL、root 内核或关闭验证的脚本。
3. 确认硬件槽为 `_a`，当前未运行 DSU，宿主正常开机完成。先备份已有 `/metadata/gsi/dsu` 记录，并另外保存重要个人数据。
4. Arch 必须保留与该镜像匹配的原 ext4 持久数据卷及标记。system 镜像不是完整 userdata 备份；仅解压 system.img 不会重建原桌面用户数据。

Windows 可先读取状态：

```powershell
adb devices
adb shell getprop ro.product.device
adb shell getprop ro.boot.slot_suffix
adb shell getprop ro.gsid.image_running
adb shell getprop sys.boot_completed
```

预期依次为 socrates、_a、0、1。若连接多台设备，所有命令都应加 `-s 目标设备序列号`，不要照搬另一台手机的序列号。

## 启动已经存在的命名 DSU

辅助脚本 `enable-existing-dsu.sh` 只启用已经存在、带完成标记的命名 DSU，不导入镜像、不创建数据卷，也不替换 system 镜像。**只有原 DSU 中已经装好对应版本时才可使用。**

将脚本推到手机后，在已获得 root 的 shell 内先检查：

```sh
sh /data/local/tmp/enable-existing-dsu.sh arch --check
# 或者
sh /data/local/tmp/enable-existing-dsu.sh evox --check
```

确认检查结果后，明确执行启动：

```sh
sh /data/local/tmp/enable-existing-dsu.sh arch --boot
# 或者
sh /data/local/tmp/enable-existing-dsu.sh evox --boot
```

脚本在 `--boot` 时先保存 DSU 元数据副本，再以 `gsi_tool enable -s --dsuslot 名称` 请求单次启动，然后重启。root 获取方式以你现有配置为准；脚本不会自动提权。

若提示 DSU 不存在、完成标记不符、目录不同或检查失败，先停止。不要为了绕过检查自行创建一个 `complete` 文件、改 `install_dir` 或执行全局 `gsi_tool wipe`。已有多个 DSU 时，后者可能破坏其他系统和数据记录。

## 新安装或替换镜像

本包没有提供未经复验的一键全新安装器。现有镜像保存不等于“任意新数据卷可直接启动”：Arch 的持久卷绑定、命名 DSU 元数据和 EvoX 的内核／厂商层依赖都需要匹配。请在备份后制定单独的安装或迁移方案，不要用旧实验脚本覆盖已有槽位。

## 返回 HyperOS

正常退出并重启，单次 DSU 启动预期返回宿主。桌面无响应时可长按电源重启；本轮测试曾通过这种方式返回宿主。回到 HyperOS 后再检查 `ro.gsid.image_running=0`。如果仍存在重复启动或元数据异常，保留备份后诊断；不要尝试切换硬件槽 b、刷 vendor_boot、清空 userdata 或格式化 metadata。

recovery 是独立用途，**不是 DSU 系统**；其使用方式见 recovery 包自己的说明。
