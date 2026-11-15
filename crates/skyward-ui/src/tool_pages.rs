use crate::app::{Action, Confirm, Frame, Hit, Place, Screen};
use crate::geom::Rect;
use crate::pages::{self, Ctx};
use crate::widgets::{self, Row, Trail};
use skyward_backup::Part;
use skyward_modules::root::{Module, Operation};

fn module_status(cx: &Ctx, module: &Module) -> &'static str {
    cx.state.lang.pick(if module.problem.is_some() {
        ["无法管理", "無法管理", "Unavailable"]
    } else if module.remove {
        ["待卸载", "待解除安裝", "Pending removal"]
    } else if module.disabled {
        ["已禁用", "已停用", "Disabled"]
    } else {
        ["已启用", "已啟用", "Enabled"]
    })
}

fn notice(cx: &Ctx, c: &mut crate::Canvas, y: f32) -> f32 {
    let l = cx.state.lang;
    if cx.state.tools_busy {
        widgets::info_card(
            c,
            cx.t,
            cx.f,
            y + cx.t.m.card_gap,
            &[(
                l.pick(["正在处理", "正在處理", "Working"]),
                l.pick(["请等待操作完成", "請等待操作完成", "Wait for the operation to finish"]).into(),
            )],
        )
    } else if let Some(message) = &cx.state.tools_notice {
        widgets::info_card(c, cx.t, cx.f, y + cx.t.m.card_gap, &[(l.pick(["操作结果", "操作結果", "Result"]), message.clone())])
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
            y = widgets::info_card(&mut c, t, f, y, &[(l.pick(["无法读取模块", "無法讀取模組", "Couldn't read modules"]), why.clone())]);
            let list = widgets::list(
                &mut c,
                t,
                f,
                y + t.m.card_gap,
                &[
                    Row::new(l.pick(["打开 data 分区", "開啟 data 分割區", "Open the data partition"])),
                    Row::new(l.pick(["重新读取", "重新讀取", "Read again"])),
                ],
            );
            out.extend(pages::hits(&list.rows, &[Action::Open(Screen::Decrypt), Action::Open(Screen::RootModules)]));
            y = list.bottom;
        }
        Some(Ok(catalog)) => {
            let manager = match (catalog.kernelsu, catalog.magisk) {
                (true, true) => "KernelSU / Magisk",
                (true, false) => "KernelSU",
                (false, true) => "Magisk",
                _ => "/data/adb/modules",
            };
            let count = catalog.modules.len();
            let heading = crate::tr!(l, "{manager} · {count} 个模块", "{manager} · {count} 個模組", "{manager} · {count} modules");
            y = widgets::section_first(&mut c, t, f, y, &heading);
            let subs: Vec<String> = catalog
                .modules
                .iter()
                .map(|m| {
                    let status = module_status(cx, m);
                    if m.staged {
                        let update = l.pick(["有待更新副本", "有待更新副本", "Update staged"]);
                        format!("{status} · {update}")
                    } else {
                        status.into()
                    }
                })
                .collect();
            let rows: Vec<Row> = catalog.modules.iter().zip(&subs).map(|(m, sub)| Row::new(&m.name).subtitle(sub)).collect();
            let list = widgets::list(&mut c, t, f, y, &rows);
            out.extend(list.rows.iter().enumerate().map(|(k, &rect)| Hit { rect, action: Action::RootPick(Some(k)) }));
            y = list.bottom;
            if !rows.is_empty() {
                y = widgets::section(&mut c, t, f, y, l.pick(["应急操作", "緊急操作", "Rescue"]));
                let rescue = widgets::list(
                    &mut c,
                    t,
                    f,
                    y,
                    &[Row::new(l.pick(["禁用所有模块", "停用所有模組", "Disable all modules"]))
                        .subtitle(l.pick(["下次启动时跳过全部模块", "下次啟動時略過所有模組", "Skip all modules on the next boot"]))
                        .tint(t.pal.danger)],
                );
                out.extend(pages::hits(&rescue.rows, &[Action::RootPick(None)]));
                y = rescue.bottom;
                y = widgets::footnote(
                    &mut c,
                    t,
                    f,
                    y,
                    l.pick([
                        "模块更改在下次正常启动时生效。",
                        "模組變更在下次正常啟動時生效。",
                        "Module changes take effect on the next normal boot.",
                    ]),
                );
            } else {
                y = widgets::info_card(
                    &mut c,
                    t,
                    f,
                    y,
                    &[(
                        l.pick(["暂无模块", "尚無模組", "No modules"]),
                        l.pick(["未发现可管理的模块", "未找到可管理的模組", "No manageable modules found"]).into(),
                    )],
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
    Frame::new(t, pages::fit(c, t, y), out, l.pick(["Root 模块管理", "Root 模組管理", "Root modules"]), t.m.content_top)
}

pub fn module(cx: &Ctx) -> Frame {
    let (t, f, s, l) = (cx.t, cx.f, cx.state, cx.state.lang);
    let mut c = pages::long_page(cx);
    let mut out = Vec::new();
    let mut y = t.m.content_top;
    let module = s.root_modules.as_ref().and_then(|r| r.as_ref().ok()).and_then(|catalog| s.root_pick.and_then(|k| catalog.modules.get(k)));
    let title = if module.is_some() {
        l.pick(["模块设置", "模組設定", "Module settings"])
    } else {
        l.pick(["禁用所有模块", "停用所有模組", "Disable all modules"])
    };
    if let Some(m) = module {
        y = widgets::section_first(&mut c, t, f, y, l.pick(["模块信息", "模組資訊", "Module information"]));
        y = widgets::info_card(
            &mut c,
            t,
            f,
            y,
            &[
                (l.pick(["模块名称", "模組名稱", "Module"]), format!("{}\n{}", m.name, m.id)),
                (l.pick(["版本与作者", "版本與作者", "Version and author"]), format!("{} · {}", m.version, m.author)),
                (l.pick(["当前状态", "目前狀態", "Current state"]), module_status(cx, m).into()),
            ],
        );
        if !m.description.is_empty() {
            y = widgets::footnote(&mut c, t, f, y, &m.description);
        }
        if let Some(why) = &m.problem {
            y = widgets::info_card(&mut c, t, f, y + t.m.card_gap, &[(l.pick(["无法操作", "無法操作", "Unavailable"]), why.clone())]);
        }
        y = widgets::section(&mut c, t, f, y, l.pick(["选择操作", "選擇操作", "Choose an action"]));
        let names = [
            l.pick(["启用", "啟用", "Enable"]),
            l.pick(["禁用", "停用", "Disable"]),
            l.pick(["下次启动时卸载", "下次啟動時解除安裝", "Remove next boot"]),
            l.pick(["取消卸载", "取消解除安裝", "Cancel removal"]),
        ];
        let operations = [Operation::Enable, Operation::Disable, Operation::Remove, Operation::UndoRemove];
        let rows: Vec<Row> =
            names.iter().zip(operations).map(|(name, op)| Row::new(name).trail(Trail::Check(s.root_operation == op))).collect();
        let list = widgets::list(&mut c, t, f, y, &rows);
        out.extend(pages::hits(&list.rows, &operations.map(Action::RootOperation)));
        y = list.bottom;
    } else if let Some(Ok(catalog)) = &s.root_modules {
        let count = catalog.modules.len();
        let detail = crate::tr!(
            l,
            "将禁用 {count} 个模块，保留模块文件。",
            "將停用 {count} 個模組，保留模組檔案。",
            "Disable {count} modules while keeping their files."
        );
        y = widgets::info_card(&mut c, t, f, y, &[(l.pick(["操作范围", "操作範圍", "Scope"]), detail)]);
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
        out.push(Hit { rect: track, action: Action::Confirm(Confirm::RootModule) });
        y = track.y + track.h;
    }
    Frame::new(t, pages::fit(c, t, y), out, title, t.m.content_top)
}

pub fn avb(cx: &Ctx) -> Frame {
    let (t, f, s, l) = (cx.t, cx.f, cx.state, cx.state.lang);
    let mut c = pages::long_page(cx);
    let mut out = Vec::new();
    let mut y = widgets::section_first(&mut c, t, f, t.m.content_top, l.pick(["当前状态", "目前狀態", "Current state"]));
    match &s.avb {
        None => y = widgets::paragraph(&mut c, t, f, y, l.pick(["正在读取 vbmeta", "正在讀取 vbmeta", "Reading vbmeta"])),
        Some(Err(why)) => {
            y = widgets::info_card(&mut c, t, f, y, &[(l.pick(["无法读取 AVB", "無法讀取 AVB", "Couldn't read AVB"]), why.clone())])
        }
        Some(Ok(status)) => {
            let suffix = status.slot.suffix();
            let state = |bit| {
                l.pick(if status.flags & bit == 0 { ["开启", "開啟", "Enabled"] } else { ["已禁用", "已停用", "Disabled"] })
            };
            let partition = format!("vbmeta{suffix}");
            y = widgets::list(
                &mut c,
                t,
                f,
                y,
                &[
                    Row::new(l.pick(["当前分区", "目前分割區", "Partition"])).trail(Trail::Text(&partition)),
                    Row::new("dm-verity").trail(Trail::Text(state(1))),
                    Row::new(l.pick(["AVB 验证", "AVB 驗證", "AVB verification"])).trail(Trail::Text(state(2))),
                ],
            )
            .bottom;
            y = widgets::section(&mut c, t, f, y, l.pick(["要禁用的校验", "要停用的驗證", "Checks to disable"]));
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
    y = widgets::section(&mut c, t, f, y, l.pick(["原始 vbmeta 备份", "原始 vbmeta 備份", "Original vbmeta backup"]));
    let list = widgets::list(
        &mut c,
        t,
        f,
        y,
        &[
            Row::new(l.pick(["内部存储", "內部儲存空間", "Internal storage"])).trail(Trail::Check(s.backup_to == Place::Storage)),
            Row::new(l.pick(["U 盘", "USB 隨身碟", "USB drive"])).trail(Trail::Check(s.backup_to == Place::UsbDrive)),
        ],
    );
    out.extend(pages::hits(&list.rows, &[Action::BackupTo(Place::Storage), Action::BackupTo(Place::UsbDrive)]));
    y = widgets::footnote(&mut c, t, f, list.bottom, l.pick([
        "仅修改当前槽位。先保存并校验原始 vbmeta，备份失败则停止；禁用后设备将不再检查所选启动完整性。",
        "僅修改目前槽位。先儲存並驗證原始 vbmeta，備份失敗即停止；停用後裝置不再檢查所選開機完整性。",
        "Current slot only. Save and verify original vbmeta first; a failed backup stops the operation. Selected boot integrity checks will be disabled.",
    ]));
    y = notice(cx, &mut c, y);
    if !s.tools_busy {
        let (label, danger) = pages::confirm_label(Confirm::DisableAvb, s);
        let track = widgets::confirm(&mut c, t, f, y + t.m.dp(24.0), label, danger, s.confirm);
        out.push(Hit { rect: track, action: Action::Confirm(Confirm::DisableAvb) });
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
        s.restore.as_ref().and_then(|(entry, _)| entry.held.as_ref().ok()).map(|h| &h.partitions)
    } else {
        s.backup_partitions.as_ref().and_then(|r| r.as_ref().ok())
    };
    let include = if restore { &s.restore_include } else { &s.backup_include };
    let groups = if restore { s.restore.as_ref().map_or([false; 4], |(_, parts)| *parts) } else { s.backup };
    if let Some(available) = available {
        let selected =
            |p: &skyward_backup::selection::Partition| include.as_ref().map_or(groups[p.part.index()], |names| names.contains(&p.name));
        let count = available.iter().filter(|p| selected(p)).count();
        let total = available.iter().filter(|p| selected(p)).fold(0u64, |sum, p| sum.saturating_add(p.bytes.unwrap_or(0)));
        let mut size = crate::fmt::bytes(total);
        if available.iter().any(|p| selected(p) && p.bytes.is_none()) {
            size = crate::tr!(l, "至少 {size}", "至少 {size}", "At least {size}");
        }
        let chosen = crate::tr!(l, "已选 {count} 项", "已選 {count} 項", "{count} selected");
        let scope = cx
            .info
            .slot
            .map(|s| {
                let slot = s.letter();
                crate::tr!(l, "当前槽位 {slot}", "目前槽位 {slot}", "Current slot {slot}")
            })
            .unwrap_or_else(|| l.pick(["仅处理所选项目", "僅處理所選項目", "Selected items only"]).into());
        y = widgets::list(&mut c, t, f, y, &[Row::new(&chosen).subtitle(&scope).trail(Trail::Text(&size))]).bottom;
        if !restore {
            y += t.m.card_gap;
            let gap = t.m.card_gap;
            let w = (t.m.w as f32 - t.m.margin * 2.0 - gap) / 2.0;
            let h = t.m.dp(44.0);
            for (k, (label, all)) in
                [(l.pick(["全选镜像", "全選映像", "Select images"]), true), (l.pick(["清空选择", "清空選擇", "Clear selection"]), false)]
                    .into_iter()
                    .enumerate()
            {
                let x = t.m.margin + k as f32 * (w + gap);
                c.fill_round_rect(x, y, w, h, h / 2.0, t.pal.card);
                c.text_vc(&f.heading, label, x + t.m.inner, y + h / 2.0, t.m.sub_px, t.pal.accent, Some(w - t.m.inner * 2.0));
                out.push(Hit { rect: Rect::new(x, y, w, h), action: Action::BackupItemsAll(all) });
            }
            y += h;
        }
        for part in Part::ALL {
            let members: Vec<_> = available.iter().enumerate().filter(|(_, p)| p.part == part).collect();
            if members.is_empty() {
                continue;
            }
            y = widgets::section(&mut c, t, f, y, l.pick(crate::app::BACKUP_PARTS[part.index()]));
            let subtitles: Vec<String> = members
                .iter()
                .map(|(_, p)| {
                    p.bytes.map(crate::fmt::bytes).unwrap_or_else(|| {
                        l.pick(["文件备份，需全部用户解密", "檔案備份，需所有使用者解密", "File archive; unlock all users first"]).into()
                    })
                })
                .collect();
            let rows: Vec<Row> = members
                .iter()
                .zip(&subtitles)
                .map(|((_, p), sub)| Row::new(&p.name).subtitle(sub).trail(Trail::Check(selected(p))))
                .collect();
            let list = widgets::list(&mut c, t, f, y, &rows);
            out.extend(list.rows.iter().zip(&members).map(|(&rect, &(index, _))| Hit {
                rect,
                action: if restore { Action::RestoreItem(index) } else { Action::BackupItem(index) },
            }));
            y = list.bottom;
        }
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
    Frame::new(t, pages::fit(c, t, y), out, l.pick(["选择具体分区", "選擇個別分割區", "Select partitions"]), t.m.content_top)
}
