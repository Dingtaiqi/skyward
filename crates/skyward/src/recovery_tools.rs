//! Module state and current-slot AVB work, off the UI thread.

use crate::backup;
use anyhow::{ensure, Context, Result};
use skyward_block::mount;
use skyward_install::avb::{self, Flags};
use skyward_modules::root::{self, Operation};
use skyward_ui::app::AvbStatus;
use skyward_vab::SnapshotState;
use std::fs::{self, File};
use std::io::{Cursor, ErrorKind, Read};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::os::unix::fs::MetadataExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::AtomicBool;
use std::sync::mpsc::{self, Receiver};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

const ADB: &str = "/data/adb";
const CACHES: [&str; 3] = [
    "/metadata/vold/ksu/modules.rc",
    "/metadata/watchdog/ksu/modules.rc",
    "/metadata/ksu/modules.rc",
];

pub enum Task {
    Module {
        id: Option<String>,
        operation: Operation,
    },
    Avb {
        expected: AvbStatus,
        checks: [bool; 2],
        backup: Box<backup::BackupTask>,
    },
}

pub struct Worker {
    events: Receiver<Result<String, String>>,
    wake: OwnedFd,
    thread: Option<JoinHandle<()>>,
}

impl Worker {
    pub fn start(task: Task) -> Result<Self> {
        let mut fds = [0; 2];
        ensure!(
            unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC | libc::O_NONBLOCK) } == 0,
            "create tools wake pipe: {}",
            std::io::Error::last_os_error()
        );
        let (wake, signal) = unsafe { (OwnedFd::from_raw_fd(fds[0]), OwnedFd::from_raw_fd(fds[1])) };
        let (send, events) = mpsc::channel();
        let thread = thread::Builder::new().name("recovery-tools".into()).spawn(move || {
            let result = match task {
                Task::Module { id, operation } => modules(id.as_deref(), operation),
                Task::Avb { expected, checks, backup } => disable_avb(expected, checks, *backup),
            }
            .map_err(|e| format!("{e:#}"));
            let _ = send.send(result);
            unsafe { libc::write(signal.as_raw_fd(), [1u8].as_ptr().cast(), 1) };
        })?;
        Ok(Self {
            events,
            wake,
            thread: Some(thread),
        })
    }

    pub fn fd(&self) -> RawFd {
        self.wake.as_raw_fd()
    }
    pub fn ended(&self) -> Option<Result<String, String>> {
        self.events.try_recv().ok()
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        if let Some(thread) = self.thread.take() {
            thread.join().expect("the recovery tools worker does not panic");
        }
    }
}

pub fn avb_status() -> Result<AvbStatus> {
    let slot = skyward_vab::current_slot()?.context("the kernel did not name an A/B slot")?;
    let path = Path::new(skyward_block::BY_NAME).join(format!("vbmeta{}", slot.suffix()));
    let mut header = [0u8; 256];
    File::open(&path)
        .with_context(|| format!("open {}", path.display()))?
        .read_exact(&mut header)?;
    Ok(AvbStatus {
        slot,
        flags: Flags::of(&header)?.0,
        unlocked: skyward_block::boot_param("androidboot.vbmeta.device_state")?.as_deref() == Some("unlocked"),
        snapshot: skyward_vab::snapshot_state()?,
    })
}

fn disable_avb(expected: AvbStatus, checks: [bool; 2], backup: backup::BackupTask) -> Result<String> {
    ensure!(
        expected.unlocked && expected.snapshot == SnapshotState::None,
        "an unlocked bootloader and no OTA snapshot are required"
    );
    ensure!(avb_status()? == expected, "the slot or AVB state changed; reread it");
    let path = Path::new(skyward_block::BY_NAME).join(format!("vbmeta{}", expected.slot.suffix()));
    let size = skyward_block::size_bytes(&path)?;
    ensure!((256..=16 * 1024 * 1024).contains(&size), "unexpected vbmeta partition size");
    let mut original = vec![0u8; size as usize];
    File::open(&path)?.read_exact(&mut original)?;
    let changed = avb::disabled(&original, Flags(expected.flags), checks)?;
    let folder = backup::backup(backup, &|_| {}, &AtomicBool::new(false)).context("save the original vbmeta before any change")?;
    let apply = (|| -> Result<()> {
        let manifest = skyward_backup::Manifest::read(&folder)?;
        ensure!(
            manifest.items.len() == 1 && manifest.items[0].name == "vbmeta",
            "the AVB backup must contain only original vbmeta"
        );
        let stored = &manifest.items[0].stored;
        let mut reader = skyward_backup::store::Reader::open(&folder, stored);
        let mut saved = Vec::new();
        reader.read_to_end(&mut saved)?;
        reader.check(stored)?;
        ensure!(
            saved == original,
            "vbmeta backup differs from the image prepared for this operation"
        );
        ensure!(avb_status()? == expected, "the slot or AVB state changed during backup");
        let mut current = vec![0; original.len()];
        File::open(&path)?.read_exact(&mut current)?;
        ensure!(current == original, "vbmeta changed during backup");
        skyward_install::image::flash(&mut Cursor::new(changed), size, &[path], &mut |_, _| {})?;
        Ok(())
    })();
    apply.with_context(|| format!("AVB operation incomplete; original backup is in {}", folder.display()))?;
    Ok(format!(
        "AVB flags updated and read back on slot {}. Original backup: {}",
        expected.slot.letter(),
        folder.display()
    ))
}

fn cache_stamp() -> Vec<(PathBuf, u64, i64, i64)> {
    CACHES
        .iter()
        .filter_map(|p| fs::metadata(p).ok().map(|m| (PathBuf::from(p), m.ino(), m.mtime(), m.mtime_nsec())))
        .collect()
}

fn modules(id: Option<&str>, operation: Operation) -> Result<String> {
    ensure!(
        mount::is_mount_point(Path::new("/data"))? && !mount::is_read_only(Path::new("/data"))?,
        "open data writable before managing modules"
    );
    let catalog = root::scan(Path::new(ADB))?;
    let ids: Vec<&str> = match id {
        Some(id) => vec![id],
        None => {
            ensure!(operation == Operation::Disable, "only bulk disable is supported");
            catalog.modules.iter().map(|m| m.id.as_str()).collect()
        }
    };
    ensure!(!ids.is_empty(), "no modules found");
    for id in &ids {
        let module = catalog
            .modules
            .iter()
            .find(|m| m.id == *id)
            .context("the selected module disappeared")?;
        ensure!(
            module.problem.is_none(),
            "module {id} is unavailable: {}",
            module.problem.as_deref().unwrap_or_default()
        );
    }
    let before = cache_stamp();
    let mut cached = !before.is_empty();
    for id in &ids { cached |= root::preinit_present(Path::new("/metadata"), id)?; }
    let ksud = ["/data/adb/ksud", "/data/adb/ksu/bin/ksud"]
        .into_iter()
        .find_map(|p| fs::canonicalize(p).ok().filter(|p| p.starts_with(ADB) && p.is_file()));
    if catalog.kernelsu {
        ensure!(
            mount::is_mount_point(Path::new("/metadata"))?,
            "mount metadata before managing KernelSU modules"
        );
        ensure!(
            !cached || ksud.is_some(),
            "KernelSU preinit cache exists but its matching ksud is missing"
        );
    }
    for id in &ids {
        root::change(Path::new(ADB), id, operation)?;
    }
    if catalog.kernelsu {
        if let Some(ksud) = ksud {
            let readonly = mount::is_read_only(Path::new("/metadata"))?;
            if readonly {
                mount::remount_writable(Path::new("/metadata"), true)?;
            }
            let refreshed = bounded_refresh(&ksud);
            let restored = if readonly {
                mount::remount_writable(Path::new("/metadata"), false)
            } else {
                Ok(())
            };
            restored.context("restore metadata mount mode after refreshing KernelSU")?;
            let required = cached
                || ids.iter().any(|id| {
                    ["initrc", "preinit"].iter().any(|name| {
                        ["modules", "modules_update"]
                            .iter()
                            .any(|parent| Path::new(ADB).join(parent).join(id).join(name).is_dir())
                    })
                });
            if required {
                refreshed.context("module markers saved, but KernelSU preinit refresh failed")?;
                ensure!(
                    cache_stamp() != before,
                    "module markers saved, but no refreshed KernelSU preinit cache was verified"
                );
                if matches!(operation, Operation::Disable | Operation::Remove) {
                    for id in &ids {
                        ensure!(!root::preinit_present(Path::new("/metadata"), id)?, "module markers saved, but cached preinit programs of {id} remain enabled");
                    }
                }
            }
        }
    }
    Ok(format!("{} module(s) updated; changes take effect next normal boot.", ids.len()))
}

/// No shell, no module script, bounded time and output. In particular an
/// installed ksud cannot block the recovery's UI or fill its log memory.
fn bounded_refresh(program: &Path) -> Result<()> {
    let mut fds = [0; 2];
    ensure!(
        unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC) } == 0,
        "create ksud output pipe"
    );
    let mut output = unsafe { File::from_raw_fd(fds[0]) };
    let writer = unsafe { File::from_raw_fd(fds[1]) };
    ensure!(
        unsafe { libc::fcntl(output.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK) } == 0,
        "make ksud output nonblocking"
    );
    let mut child = Command::new(program)
        .args(["initrc", "refresh"])
        .process_group(0)
        .stdin(Stdio::null())
        .stdout(writer.try_clone()?)
        .stderr(writer)
        .spawn()
        .context("run installed ksud initrc refresh")?;
    let started = Instant::now();
    let mut bytes = Vec::new();
    let mut buffer = [0; 4096];
    let mut ended = None;
    loop {
        let mut eof = false;
        loop {
            match output.read(&mut buffer) {
                Ok(0) => {
                    eof = true;
                    break;
                }
                Ok(n) => {
                    bytes.extend_from_slice(&buffer[..n]);
                    if bytes.len() > 256 * 1024 {
                        break;
                    }
                }
                Err(e) if e.kind() == ErrorKind::WouldBlock => break,
                Err(e) if e.kind() == ErrorKind::Interrupted => continue,
                Err(e) => {
                    unsafe { libc::kill(-(child.id() as i32), libc::SIGKILL) };
                    let _ = child.wait();
                    return Err(e).context("read ksud output");
                }
            }
        }
        if ended.is_none() {
            ended = child.try_wait()?;
        }
        if let Some(status) = ended {
            if eof {
                let said = String::from_utf8_lossy(&bytes[..bytes.len().min(4096)]);
                ensure!(
                    status.success() && !said.contains("regenerate preinit failed"),
                    "ksud refresh failed: {said}"
                );
                return Ok(());
            }
        }
        if started.elapsed() > Duration::from_secs(10) || bytes.len() > 256 * 1024 {
            unsafe { libc::kill(-(child.id() as i32), libc::SIGKILL) };
            let _ = child.wait();
            anyhow::bail!("ksud refresh exceeded its time or output limit");
        }
        thread::sleep(Duration::from_millis(20));
    }
}
