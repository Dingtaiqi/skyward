use crate::app::{Action, Confirm, Frame, Hit, Place, Screen};
use crate::pages::{self, Ctx};
use crate::widgets::{self, Row, Trail};
use skyward_modules::root::Operation;

fn notice(cx: &Ctx, c: &mut crate::Canvas, y: f32) -> f32 {
    let l = cx.state.lang;
    if cx.state.tools_busy {
        widgets::footnote(
            c,
            cx.t,
            cx.f,
            y,
            l.pick(["正在处理，请等待完成", "正在處理，請等待完成", "Working; wait for completion"]),
        )
    } else if let Some(message) = &cx.state.tools_notice {
        widgets::paragraph(c, cx.t, cx.f, y, message)
    } else {
        y
    }
}

pub fn modules(cx: &Ctx) -> Frame {
    let (t, f, s, l) = (cx.t, cx.f, cx.state, cx.state.lang);
    let mut c = pages::long_page(cx);
    let mut out = Vec::new();
    let mut y = t.m.content_top;
    match &s.root_modules {
        None => y = widgets::paragraph(&mut c, t, f, y, l.pick(["正在读取模块", "正在讀取模組", "Reading modules"])),
        Some(Err(why)) => {
            y = widgets::paragraph(&mut c, t, f, y, why);
            let list = widgets::list(
                &mut c,
                t,
                f,
                y,
                &[
                    Row::new(l.pick(["打开 data 分区", "開啟 data 分割區", "Open the data partition"])),
                    Row::new(l.pick(["重新读取", "重新讀取", "Read again"])),
                ],
            );
            out.extend(pages::hits(
                &list.rows,
                &[Action::Open(Screen::Decrypt), Action::Open(Screen::RootModules)],
            ));
            y = list.bottom;
        }
        Some(Ok(catalog)) => {
            let manager = match (catalog.kernelsu, catalog.magisk) {
                (true, true) => "KernelSU / Magisk",
                (true, false) => "KernelSU",
                (false, true) => "Magisk",
                _ => "/data/adb/modules",
            };
            y = widgets::section_first(&mut c, t, f, y, manager);
            let subs: Vec<String> = catalog
                .modules
                .iter()
                .map(|m| {
                    let status = l.pick(if m.problem.is_some() {
                        ["无法管理", "無法管理", "Unavailable"]
                    } else if m.remove {
                        ["待卸载", "待解除安裝", "Pending removal"]
                    } else if m.disabled {
                        ["已禁用", "已停用", "Disabled"]
                    } else {
                        ["下次启动启用", "下次啟動啟用", "Enabled next boot"]
                    });
                    format!("{} · {} · {status}{}", m.id, m.version, if m.staged { " · update" } else { "" })
                })
                .collect();
            let rows: Vec<Row> = catalog
                .modules
                .iter()
                .zip(&subs)
                .map(|(m, sub)| Row::new(&m.name).subtitle(sub))
                .collect();
            let list = widgets::list(&mut c, t, f, y, &rows);
            out.extend(list.rows.iter().enumerate().map(|(k, &rect)| Hit {
                rect,
                action: Action::RootPick(Some(k)),
            }));
            y = list.bottom;
            if !rows.is_empty() {
                let rescue = widgets::list(
                    &mut c,
                    t,
                    f,
                    y,
                    &[Row::new(l.pick(["禁用所有模块", "停用所有模組", "Disable all modules"]))],
                );
                out.extend(pages::hits(&rescue.rows, &[Action::RootPick(None)]));
                y = rescue.bottom;
            } else {
                y = widgets::paragraph(
                    &mut c,
                    t,
                    f,
                    y,
                    l.pick(["未发现可管理的模块", "未找到可管理的模組", "No manageable modules found"]),
                );
            }
            if catalog.legacy_image && catalog.modules.is_empty() {
                y = widgets::footnote(
                    &mut c,
                    t,
                    f,
                    y,
                    l.pick([
                        "检测到旧版模块镜像，需要对应版本的管理器处理",
                        "偵測到舊版模組映像，需要對應版本的管理員處理",
                        "Legacy module images require their matching manager",
                    ]),
                );
            }
        }
    }
    y = notice(cx, &mut c, y);
    Frame::new(
        t,
        pages::fit(c, t, y),
        out,
        l.pick(["Root 模块管理", "Root 模組管理", "Root modules"]),
        t.m.content_top,
    )
}

pub fn module(cx: &Ctx) -> Frame {
    let (t, f, s, l) = (cx.t, cx.f, cx.state, cx.state.lang);
    let mut c = pages::long_page(cx);
    let mut out = Vec::new();
    let mut y = t.m.content_top;
    let module = s
        .root_modules
        .as_ref()
        .and_then(|r| r.as_ref().ok())
        .and_then(|catalog| s.root_pick.and_then(|k| catalog.modules.get(k)));
    let title = if module.is_some() {
        l.pick(["模块设置", "模組設定", "Module settings"])
    } else {
        l.pick(["禁用所有模块", "停用所有模組", "Disable all modules"])
    };
    if let Some(m) = module {
        y = widgets::paragraph(
            &mut c,
            t,
            f,
            y,
            &format!("{}\n{}\n{} · {}\n{}", m.name, m.id, m.version, m.author, m.description),
        );
        if let Some(why) = &m.problem {
            y = widgets::paragraph(&mut c, t, f, y, why);
        }
        let names = [
            l.pick(["启用", "啟用", "Enable"]),
            l.pick(["禁用", "停用", "Disable"]),
            l.pick(["下次启动时卸载", "下次啟動時解除安裝", "Remove next boot"]),
            l.pick(["取消卸载", "取消解除安裝", "Cancel removal"]),
        ];
        let operations = [Operation::Enable, Operation::Disable, Operation::Remove, Operation::UndoRemove];
        let rows: Vec<Row> = names
            .iter()
            .zip(operations)
            .map(|(name, op)| Row::new(name).trail(Trail::Check(s.root_operation == op)))
            .collect();
        let list = widgets::list(&mut c, t, f, y, &rows);
        out.extend(pages::hits(&list.rows, &operations.map(Action::RootOperation)));
        y = list.bottom;
    }
    y = widgets::footnote(
        &mut c,
        t,
        f,
        y,
        l.pick([
            "更改在下次正常启动时生效。卸载由 Root 框架执行，REC 不运行模块脚本。",
            "變更在下次正常啟動時生效。解除安裝由 Root 框架執行，REC 不執行模組指令碼。",
            "Changes take effect next normal boot. The root framework removes modules; recovery does not run their scripts.",
        ]),
    );
    y = notice(cx, &mut c, y);
    if !s.tools_busy && module.is_none_or(|m| m.problem.is_none()) {
        let (label, danger) = pages::confirm_label(Confirm::RootModule, s);
        let track = widgets::confirm(&mut c, t, f, y + t.m.dp(24.0), label, danger, s.confirm);
        out.push(Hit {
            rect: track,
            action: Action::Confirm(Confirm::RootModule),
        });
        y = track.y + track.h;
    }
    Frame::new(t, pages::fit(c, t, y), out, title, t.m.content_top)
}

pub fn avb(cx: &Ctx) -> Frame {
    let (t, f, s, l) = (cx.t, cx.f, cx.state, cx.state.lang);
    let mut c = pages::long_page(cx);
    let mut out = Vec::new();
    let mut y = t.m.content_top;
    match &s.avb {
        None => y = widgets::paragraph(&mut c, t, f, y, l.pick(["正在读取 vbmeta", "正在讀取 vbmeta", "Reading vbmeta"])),
        Some(Err(why)) => y = widgets::paragraph(&mut c, t, f, y, why),
        Some(Ok(status)) => {
            let slot = status.slot.letter();
            let state = |bit| {
                l.pick(if status.flags & bit == 0 {
                    ["开启", "開啟", "Enabled"]
                } else {
                    ["已禁用", "已停用", "Disabled"]
                })
            };
            y = widgets::paragraph(
                &mut c,
                t,
                f,
                y,
                &format!("vbmeta_{slot}\ndm-verity: {}\nAVB: {}", state(1), state(2)),
            );
            let list = widgets::list(
                &mut c,
                t,
                f,
                y,
                &[
                    Row::new(l.pick(["禁用 dm-verity", "停用 dm-verity", "Disable dm-verity"])).trail(Trail::Check(s.avb_checks[0])),
                    Row::new(l.pick(["禁用 AVB 验证", "停用 AVB 驗證", "Disable AVB verification"])).trail(Trail::Check(s.avb_checks[1])),
                ],
            );
            out.extend(pages::hits(&list.rows, &[Action::AvbCheck(0), Action::AvbCheck(1)]));
            y = list.bottom;
            if !status.unlocked || status.snapshot != skyward_vab::SnapshotState::None {
                y = widgets::footnote(
                    &mut c,
                    t,
                    f,
                    y,
                    l.pick([
                        "需先解锁 Bootloader 并完成 OTA 合并",
                        "需先解鎖 Bootloader 並完成 OTA 合併",
                        "An unlocked bootloader and no pending OTA are required",
                    ]),
                );
            }
        }
    }
    let list = widgets::list(
        &mut c,
        t,
        f,
        y,
        &[
            Row::new(l.pick([
                "原始 vbmeta 备份到内部存储",
                "原始 vbmeta 備份至內部儲存空間",
                "Back up original vbmeta to internal storage",
            ]))
            .trail(Trail::Check(s.backup_to == Place::Storage)),
            Row::new(l.pick([
                "原始 vbmeta 备份到 U 盘",
                "原始 vbmeta 備份至 USB 隨身碟",
                "Back up original vbmeta to USB drive",
            ]))
            .trail(Trail::Check(s.backup_to == Place::UsbDrive)),
        ],
    );
    out.extend(pages::hits(
        &list.rows,
        &[Action::BackupTo(Place::Storage), Action::BackupTo(Place::UsbDrive)],
    ));
    y = widgets::footnote(&mut c, t, f, list.bottom, l.pick([
        "仅修改当前槽位。先保存并校验原始 vbmeta，备份失败则停止；禁用后设备将不再检查所选启动完整性。",
        "僅修改目前槽位。先儲存並驗證原始 vbmeta，備份失敗即停止；停用後裝置不再檢查所選開機完整性。",
        "Current slot only. Save and verify original vbmeta first; a failed backup stops the operation. Selected boot integrity checks will be disabled.",
    ]));
    y = notice(cx, &mut c, y);
    if !s.tools_busy {
        let (label, danger) = pages::confirm_label(Confirm::DisableAvb, s);
        let track = widgets::confirm(&mut c, t, f, y + t.m.dp(24.0), label, danger, s.confirm);
        out.push(Hit {
            rect: track,
            action: Action::Confirm(Confirm::DisableAvb),
        });
        y = track.y + track.h;
    }
    Frame::new(t, pages::fit(c, t, y), out, "AVB", t.m.content_top)
}

pub fn partitions(cx: &Ctx, restore: bool) -> Frame {
    let (t, f, s, l) = (cx.t, cx.f, cx.state, cx.state.lang);
    let mut c = pages::long_page(cx);
    let mut out = Vec::new();
    let mut y = t.m.content_top;
    let available = if restore {
        s.restore
            .as_ref()
            .and_then(|(entry, _)| entry.held.as_ref().ok())
            .map(|h| &h.partitions)
    } else {
        s.backup_partitions.as_ref().and_then(|r| r.as_ref().ok())
    };
    let include = if restore { &s.restore_include } else { &s.backup_include };
    let groups = if restore {
        s.restore.as_ref().map_or([false; 4], |(_, parts)| *parts)
    } else {
        s.backup
    };
    if !restore {
        let list = widgets::list(
            &mut c,
            t,
            f,
            y,
            &[
                Row::new(l.pick(["选择全部分区镜像", "選擇全部分割區映像", "Select all partition images"])),
                Row::new(l.pick(["取消全部选择", "取消全部選擇", "Deselect all"])),
            ],
        );
        out.extend(pages::hits(
            &list.rows,
            &[Action::BackupItemsAll(true), Action::BackupItemsAll(false)],
        ));
        y = list.bottom;
    }
    if let Some(available) = available {
        let subtitles: Vec<String> = available
            .iter()
            .map(|p| {
                p.bytes.map(crate::fmt::gb).unwrap_or_else(|| {
                    l.pick([
                        "data 文件备份，需全部用户解密",
                        "data 檔案備份，需所有使用者解密",
                        "Data file archive; unlock all users first",
                    ])
                    .to_owned()
                })
            })
            .collect();
        let rows: Vec<Row> = available
            .iter()
            .zip(&subtitles)
            .map(|(p, sub)| {
                let selected = include.as_ref().map_or(groups[p.part.index()], |names| names.contains(&p.name));
                Row::new(&p.name).subtitle(sub).trail(Trail::Check(selected))
            })
            .collect();
        let list = widgets::list(&mut c, t, f, y, &rows);
        out.extend(list.rows.iter().enumerate().map(|(k, &rect)| Hit {
            rect,
            action: if restore { Action::RestoreItem(k) } else { Action::BackupItem(k) },
        }));
        y = list.bottom;
    } else {
        let why = s
            .backup_partitions
            .as_ref()
            .and_then(|r| r.as_ref().err())
            .map(String::as_str)
            .unwrap_or_else(|| l.pick(["正在读取分区", "正在讀取分割區", "Reading partitions"]));
        y = widgets::paragraph(&mut c, t, f, y, why);
    }
    y = widgets::footnote(
        &mut c,
        t,
        f,
        y,
        l.pick([
            "返回上一页确认所选项目、位置和槽位后开始。",
            "返回上一頁確認所選項目、位置和槽位後開始。",
            "Go back to confirm the selection, destination and slot before starting.",
        ]),
    );
    Frame::new(
        t,
        pages::fit(c, t, y),
        out,
        l.pick(["选择具体分区", "選擇個別分割區", "Select partitions"]),
        t.m.content_top,
    )
}
