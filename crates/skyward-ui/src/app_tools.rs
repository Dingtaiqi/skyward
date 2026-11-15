use super::*;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct AvbStatus {
    pub slot: Slot,
    pub flags: u32,
    pub unlocked: bool,
    pub snapshot: SnapshotState,
}

impl App {
    pub fn set_root_modules(&mut self, result: Result<RootCatalog, String>) {
        self.state.root_modules = Some(result);
        self.refresh();
    }

    pub fn set_avb(&mut self, result: Result<AvbStatus, String>) {
        self.state.avb = Some(result);
        self.refresh();
    }

    pub fn set_backup_partitions(&mut self, result: Result<Vec<BackupPartition>, String>) {
        self.state.backup_partitions = Some(result);
        self.refresh();
    }

    pub fn tools_finished(&mut self, result: Result<String, String>) {
        self.state.tools_busy = false;
        self.state.tools_notice = Some(match result {
            Ok(message) => message,
            Err(why) => format!(
                "{}: {why}",
                self.state.lang.pick(["操作未完成", "操作未完成", "Operation incomplete"])
            ),
        });
        self.refresh();
    }

    fn recovery_action(&mut self, action: Action) -> Vec<Effect> {
        match action {
            Action::RootPick(index) => {
                self.state.root_pick = index;
                self.state.root_operation = RootOperation::Disable;
                self.state.tools_notice = None;
                self.open(Screen::RootModule);
            }
            Action::RootOperation(operation) => self.state.root_operation = operation,
            Action::AvbCheck(k) if k < 2 => self.state.avb_checks[k] = !self.state.avb_checks[k],
            Action::BackupItem(k) => {
                if let Some(Ok(available)) = &self.state.backup_partitions {
                    if let Some(item) = available.get(k) {
                        let selected = self.state.backup_include.get_or_insert_with(|| {
                            available
                                .iter()
                                .filter(|p| self.state.backup[p.part.index()])
                                .map(|p| p.name.clone())
                                .collect()
                        });
                        if selected.contains(&item.name) {
                            selected.retain(|name| name != &item.name);
                        } else {
                            selected.push(item.name.clone());
                        }
                        self.state.backup = skyward_backup::selection::selected(selected, available).unwrap_or([false; 4]);
                    }
                }
            }
            Action::BackupItemsAll(all) => {
                if let Some(Ok(available)) = &self.state.backup_partitions {
                    let selected: Vec<String> = available
                        .iter()
                        .filter(|p| all && p.part != skyward_backup::Part::Data)
                        .map(|p| p.name.clone())
                        .collect();
                    self.state.backup = skyward_backup::selection::selected(&selected, available).unwrap_or([false; 4]);
                    self.state.backup_include = Some(selected);
                }
            }
            Action::RestoreItem(k) => {
                if let Some((entry, groups)) = &mut self.state.restore {
                    if let Ok(held) = &entry.held {
                        if let Some(item) = held.partitions.get(k) {
                            let selected = self.state.restore_include.get_or_insert_with(|| {
                                held.partitions
                                    .iter()
                                    .filter(|p| groups[p.part.index()])
                                    .map(|p| p.name.clone())
                                    .collect()
                            });
                            if selected.contains(&item.name) {
                                selected.retain(|name| name != &item.name);
                            } else {
                                selected.push(item.name.clone());
                            }
                            *groups = skyward_backup::selection::selected(selected, &held.partitions).unwrap_or([false; 4]);
                        }
                    }
                }
            }
            _ => {}
        }
        self.refresh();
        Vec::new()
    }

    fn recovery_confirmed(&mut self, which: Confirm, now: u64) -> Vec<Effect> {
        let effect = match which {
            Confirm::RootModule => {
                let Some(Ok(catalog)) = &self.state.root_modules else {
                    return Vec::new();
                };
                if catalog.modules.is_empty() {
                    return Vec::new();
                }
                let id = match self.state.root_pick {
                    Some(k) => {
                        let Some(module) = catalog.modules.get(k) else { return Vec::new() };
                        if let Some(why) = &module.problem {
                            let why = why.clone();
                            self.say(&why, now);
                            return Vec::new();
                        }
                        Some(module.id.clone())
                    }
                    None => None,
                };
                Effect::RootModule {
                    operation: if id.is_none() {
                        RootOperation::Disable
                    } else {
                        self.state.root_operation
                    },
                    id,
                }
            }
            Confirm::DisableAvb => {
                let Some(Ok(status)) = self.state.avb.clone() else {
                    return Vec::new();
                };
                if !status.unlocked || status.snapshot != SnapshotState::None || !self.state.place_open(self.state.backup_to) {
                    self.say(
                        self.state.lang.pick([
                            "需已解锁、无进行中的更新，且备份位置可写",
                            "需已解鎖、無進行中的更新，且備份位置可寫",
                            "Unlock the bootloader, finish any OTA and make backup storage available",
                        ]),
                        now,
                    );
                    return Vec::new();
                }
                Effect::DisableAvb {
                    status,
                    checks: self.state.avb_checks,
                    place: self.state.backup_to,
                }
            }
            _ => return Vec::new(),
        };
        self.state.tools_busy = true;
        self.state.tools_notice = None;
        self.refresh();
        vec![effect]
    }
}
