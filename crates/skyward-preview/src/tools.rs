//! Synthetic UI fixtures, explicitly labelled as previews rather than
//! evidence of a successful operation on a real phone.
use super::*;
use skyward_backup::selection::Partition;
use skyward_modules::root::{Catalog, Module};

pub(super) fn render(out: &Path, fonts: &Fonts, cfg: &DeviceConfig) -> Result<()> {
    let info = nezha(cfg);
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
                flags: 0,
                unlocked: true,
                snapshot: SnapshotState::None,
            }));
            app.set_backup_partitions(Ok(vec![
                Partition {
                    name: "boot".into(),
                    part: skyward_backup::Part::Boot,
                    bytes: Some(96 << 20),
                },
                Partition {
                    name: "init_boot".into(),
                    part: skyward_backup::Part::Boot,
                    bytes: Some(8 << 20),
                },
                Partition {
                    name: "vendor_boot".into(),
                    part: skyward_backup::Part::Boot,
                    bytes: Some(96 << 20),
                },
                Partition {
                    name: "vbmeta".into(),
                    part: skyward_backup::Part::Boot,
                    bytes: Some(64 << 10),
                },
                Partition {
                    name: "system".into(),
                    part: skyward_backup::Part::System,
                    bytes: Some(4 << 30),
                },
                Partition {
                    name: "persist".into(),
                    part: skyward_backup::Part::Efs,
                    bytes: Some(32 << 20),
                },
            ]));
            let mut canvas = whole_page(&mut app, fonts, &info);
            canvas.text_center(
                &fonts.body,
                "界面示例 · 非实机测试结果",
                w as f32 / 2.0,
                canvas.h as f32 - 30.0,
                28.0,
                skyward_ui::color::Rgba::rgb(128, 128, 128),
            );
            write_png(&out.join(format!("{name}-{}.png", if dark { "dark" } else { "light" })), &canvas)?;
        }
    }
    Ok(())
}
