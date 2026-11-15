//! Synthetic UI fixtures, explicitly labelled as previews rather than
//! evidence of a successful operation on a real phone.
use super::*;
use skyward_backup::selection::Partition;
use skyward_modules::root::{Catalog, Module};

pub(super) fn render(out: &Path, fonts: &Fonts, cfg: &DeviceConfig) -> Result<()> {
    let mut info = nezha(cfg);
    info.model = cfg.device.model.clone();
    info.slot = Some(Slot::A);
    let (w, h) = (cfg.display.width, cfg.display.height);
    let ppd = cfg.display.density as f32 / 160.0;
    let catalog = Catalog {
        kernelsu: true,
        modules: vec![
            Module {
                id: "performance_example".into(),
                name: "性能配置（示例）".into(),
                version: "1.0".into(),
                author: "Example".into(),
                description: "示例模块，用于检查界面布局".into(),
                disabled: false,
                remove: false,
                staged: false,
                problem: None,
            },
            Module {
                id: "display_example".into(),
                name: "显示配置（示例）".into(),
                version: "2.0".into(),
                author: "Example".into(),
                description: String::new(),
                disabled: true,
                remove: false,
                staged: true,
                problem: None,
            },
        ],
        ..Catalog::default()
    };
    for (name, screen) in [
        ("modules", Screen::RootModules),
        ("module-detail", Screen::RootModule),
        ("avb", Screen::Avb),
        ("partitions", Screen::BackupPartitions),
        ("backup", Screen::Backup),
        ("restore", Screen::RestoreBackup),
        ("restore-partitions", Screen::RestorePartitions),
        ("module-long", Screen::RootModule),
        ("modules-empty", Screen::RootModules),
        ("avb-locked", Screen::Avb),
    ] {
        for dark in [false, true] {
            let mut app = App::new(screen, w, h, ppd);
            app.state.lang = lang();
            app.state.dark = dark;
            app.state.data = Data::Open;
            app.state.root_pick = Some(0);
            app.set_root_modules(Ok(catalog.clone()));
            app.set_avb(Ok(skyward_ui::app::AvbStatus {
                slot: Slot::A,
                flags: 2,
                unlocked: name != "avb-locked",
                snapshot: SnapshotState::None,
            }));
            let partitions = vec![
                Partition { name: "boot".into(), part: skyward_backup::Part::Boot, bytes: Some(96 << 20) },
                Partition { name: "init_boot".into(), part: skyward_backup::Part::Boot, bytes: Some(8 << 20) },
                Partition { name: "vendor_boot".into(), part: skyward_backup::Part::Boot, bytes: Some(96 << 20) },
                Partition { name: "vbmeta".into(), part: skyward_backup::Part::Boot, bytes: Some(64 << 10) },
                Partition { name: "system".into(), part: skyward_backup::Part::System, bytes: Some(4 << 30) },
                Partition { name: "persist".into(), part: skyward_backup::Part::Efs, bytes: Some(32 << 20) },
            ];
            app.set_backup_partitions(Ok(partitions.clone()));
            app.state.backup_include = Some(vec!["init_boot".into(), "vbmeta".into()]);
            app.state.backup = [true, false, false, false];
            app.state.restore = Some((
                BackupEntry {
                    place: Place::Storage,
                    name: "20261115-120000".into(),
                    held: Ok(Held {
                        partitions: partitions[..4].to_vec(),
                        made: 1_794_715_200,
                        size: 210 << 20,
                        parts: [true, false, false, false],
                        device: cfg.device.codename.clone(),
                        system: Some("HyperOS（界面示例）".into()),
                        slot: Some(Slot::A),
                    }),
                },
                [true, false, false, false],
            ));
            app.state.restore_include = Some(vec!["vbmeta".into()]);
            if name == "module-long" {
                let mut long = catalog.clone();
                long.modules[0].name = "兼容多个系统版本的显示与触摸配置模块（布局示例）".into();
                long.modules[0].id = "display_and_touch_compatibility_configuration_example".into();
                long.modules[0].description =
                    "这是一段较长的模块说明，用来检查文字换行、卡片间距和下方操作区域。名称或标识较长时，页面仍应可以正常滚动和选择操作。"
                        .into();
                app.set_root_modules(Ok(long));
                app.state.tools_notice = Some("示例结果：模块更改已保存，下次正常启动时生效。".into());
            } else if name == "modules-empty" {
                app.set_root_modules(Ok(Catalog { kernelsu: true, ..Catalog::default() }));
            }
            // Preserve the actual viewport, and separately render the entire
            // scrollable page so controls below the fold are also reviewable.
            let mut full = whole_page(&mut app, fonts, &info);
            let mut canvas = Canvas::new(w, h);
            app.compose(&mut canvas, fonts, &info, CLOCK, 1000);
            let mode = if dark { "dark" } else { "light" };
            let theme = app.theme();
            write_png(&out.join(format!("{name}-{mode}.png")), &labelled(canvas.clone(), &theme, fonts))?;
            if full.h > h {
                // whole_page draws the page body. Take its unchanged header
                // from the real compositor rather than re-creating one.
                full.put(&canvas.crop_height(theme.m.content_top.ceil() as u32), 0);
                write_png(&out.join(format!("{name}-{mode}-full.png")), &labelled(full, &theme, fonts))?;
            }
        }
    }
    Ok(())
}

/// An external caption strip never covers a control in the viewport.
fn labelled(canvas: Canvas, theme: &Theme, fonts: &Fonts) -> Canvas {
    let pad = theme.m.dp(36.0) as u32;
    let mut result = Canvas::new(canvas.w, canvas.h + pad);
    result.clear(theme.pal.bg);
    result.put(&canvas, 0);
    result.text_center(
        &fonts.body,
        "界面示例 · 非实机测试结果",
        canvas.w as f32 / 2.0,
        canvas.h as f32 + pad as f32 / 2.0,
        theme.m.label_px,
        theme.pal.value,
    );
    result
}
