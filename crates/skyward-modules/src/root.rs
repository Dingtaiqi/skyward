//! Offline management of Magisk and KernelSU module state. Only their
//! documented marker files are changed; no module scripts run in recovery.

use anyhow::{ensure, Context, Result};
use std::collections::{BTreeMap, BTreeSet};
use std::ffi::CString;
use std::fs::File;
use std::io::{ErrorKind, Read};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::OpenOptionsExt;
use std::path::Path;

const PROP_LIMIT: u64 = 64 * 1024;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Module {
    pub id: String,
    pub name: String,
    pub version: String,
    pub author: String,
    pub description: String,
    pub disabled: bool,
    pub remove: bool,
    pub staged: bool,
    pub problem: Option<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Catalog {
    pub modules: Vec<Module>,
    pub kernelsu: bool,
    pub magisk: bool,
    pub legacy_image: bool,
}

#[derive(Debug, Default)]
pub struct CacheContext {
    pub active_roots: Vec<std::path::PathBuf>,
    pub preinit: bool,
}

/// Empty modules.rc files need no rewrite for ordinary marker changes.
/// Nonempty init scripts and copied preinit programs require the matching
/// kernel interface before the installed ksud may rebuild their caches.
pub fn cache_context(metadata: &Path) -> Result<CacheContext> {
    let mut context = CacheContext::default();
    for base in ["vold/ksu", "watchdog/ksu", "ksu"] {
        let path = metadata.join(base);
        let dir = match Dir::open(&path) {
            Ok(dir) => dir,
            Err(e) if e.downcast_ref::<std::io::Error>().is_some_and(|e| e.kind() == ErrorKind::NotFound) => continue,
            Err(e) => return Err(e),
        };
        let mut active = false;
        match dir.file("modules.rc", libc::O_RDONLY | libc::O_NONBLOCK) {
            Ok(file) => {
                ensure!(file.metadata()?.is_file(), "modules.rc is not a regular file");
                let mut bytes = Vec::new();
                file.take(1024 * 1024 + 1).read_to_end(&mut bytes)?;
                ensure!(bytes.len() <= 1024 * 1024, "KernelSU init cache is too large");
                active |= bytes.iter().any(|b| !b.is_ascii_whitespace());
            }
            Err(e) if e.kind() == ErrorKind::NotFound => {}
            Err(e) => return Err(e).context("inspect KernelSU init cache"),
        }
        if let Some(preinit) = optional_child(&dir, "preinit")? {
            if preinit.names()?.iter().any(|name| !name.starts_with('.')) {
                context.preinit = true;
                active = true;
            }
        }
        if active {
            context.active_roots.push(path);
        }
    }
    Ok(context)
}

pub fn validate_refresh(info: &str, metadata: &Path, context: &CacheContext, needs_preinit: bool) -> Result<()> {
    let value = |key: &str| -> Result<u32> {
        let raw = info
            .lines()
            .find_map(|line| {
                line.split_once(':')
                    .filter(|(name, _)| name.trim() == key)
                    .map(|(_, value)| value.trim())
            })
            .with_context(|| format!("ksud did not report kernel {key}"))?;
        if let Some(hex) = raw.strip_prefix("0x") {
            Ok(u32::from_str_radix(hex, 16)?)
        } else {
            Ok(raw.parse()?)
        }
    };
    ensure!(value("version")? > 0, "the recovery kernel has no compatible KernelSU interface");
    let uapi = value("uapi_version")?;
    ensure!(uapi > 0, "the recovery kernel's KernelSU API is unknown");
    let flags = value("flags")?;
    // KernelSU uapi/supercall.h: KSU_GET_INFO_FLAG_PREINIT = 1U << 5.
    ensure!(
        !(context.preinit || needs_preinit) || flags & (1 << 5) != 0,
        "the recovery kernel cannot preserve KernelSU preinit programs"
    );
    let expected = if uapi >= 6 && metadata.join("vold").is_dir() {
        metadata.join("vold/ksu")
    } else if metadata.join("watchdog").is_dir() {
        metadata.join("watchdog/ksu")
    } else {
        metadata.join("ksu")
    };
    ensure!(
        context.active_roots.iter().all(|p| *p == expected),
        "the recovery kernel would migrate a different KernelSU cache layout"
    );
    Ok(())
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Operation {
    Enable,
    Disable,
    Remove,
    UndoRemove,
}

fn valid_id(id: &str) -> bool {
    !id.is_empty() && id.len() <= 128 && id != "." && id != ".." && id.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

/// Directory descriptors pin the module tree across all marker operations.
/// Neither module directories, property files nor markers follow symlinks.
struct Dir(File);

impl Dir {
    fn open(path: &Path) -> Result<Self> {
        let file = File::options()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(path)?;
        Ok(Self(file))
    }

    fn file(&self, name: &str, flags: i32) -> std::io::Result<File> {
        let name = CString::new(name).map_err(std::io::Error::other)?;
        let fd = unsafe { libc::openat(self.0.as_raw_fd(), name.as_ptr(), flags | libc::O_NOFOLLOW | libc::O_CLOEXEC, 0o600) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error());
        }
        Ok(unsafe { File::from_raw_fd(fd) })
    }

    fn child(&self, name: &str) -> std::io::Result<Self> {
        self.file(name, libc::O_RDONLY | libc::O_DIRECTORY).map(Self)
    }

    fn flag(&self, name: &str) -> Result<bool> {
        match self.file(name, libc::O_RDONLY | libc::O_NONBLOCK) {
            Ok(file) => {
                ensure!(file.metadata()?.is_file(), "{name} is not a regular marker file");
                Ok(true)
            }
            Err(e) if e.kind() == ErrorKind::NotFound => Ok(false),
            Err(e) => Err(e).with_context(|| format!("read module marker {name}")),
        }
    }

    fn mark(&self, name: &str, present: bool) -> Result<()> {
        if self.flag(name)? == present {
            return Ok(());
        }
        if present {
            let file = self.file(name, libc::O_WRONLY | libc::O_CREAT | libc::O_EXCL)?;
            file.sync_all()?;
        } else {
            let name = CString::new(name)?;
            ensure!(
                unsafe { libc::unlinkat(self.0.as_raw_fd(), name.as_ptr(), 0) } == 0,
                "remove marker: {}",
                std::io::Error::last_os_error()
            );
        }
        self.0.sync_all()?;
        Ok(())
    }

    fn properties(&self) -> Result<BTreeMap<String, String>> {
        let file = self.file("module.prop", libc::O_RDONLY | libc::O_NONBLOCK)?;
        ensure!(file.metadata()?.is_file(), "module.prop is not a regular file");
        let mut text = String::new();
        file.take(PROP_LIMIT + 1).read_to_string(&mut text)?;
        ensure!(text.len() as u64 <= PROP_LIMIT, "module.prop is too large");
        Ok(text
            .lines()
            .filter_map(|line| {
                let line = line.trim();
                if line.starts_with('#') {
                    return None;
                }
                let (name, value) = line.split_once('=')?;
                Some((name.trim().to_owned(), value.trim().to_owned()))
            })
            .collect())
    }

    fn names(&self) -> Result<Vec<String>> {
        let mut names = Vec::new();
        for entry in std::fs::read_dir(format!("/proc/self/fd/{}", self.0.as_raw_fd()))? {
            let entry = entry?;
            let Some(name) = entry.file_name().to_str().map(str::to_owned) else {
                continue;
            };
            if valid_id(&name) {
                names.push(name);
            }
        }
        Ok(names)
    }
}

fn optional_child(dir: &Dir, name: &str) -> Result<Option<Dir>> {
    match dir.child(name) {
        Ok(dir) => Ok(Some(dir)),
        Err(e) if e.kind() == ErrorKind::NotFound => Ok(None),
        Err(e) => Err(e).with_context(|| format!("open {name} without following links")),
    }
}

pub fn scan(adb: &Path) -> Result<Catalog> {
    let root = Dir::open(adb).with_context(|| format!("open {}; decrypt data first", adb.display()))?;
    let active = optional_child(&root, "modules")?;
    let staged = optional_child(&root, "modules_update")?;
    let mut catalog = Catalog {
        kernelsu: adb.join("ksud").is_file() || adb.join("ksu").is_dir(),
        magisk: adb.join("magisk").is_dir(),
        legacy_image: ["ksu/modules.img", "ksu/modules_update.img", "modules.img"]
            .iter()
            .any(|p| adb.join(p).is_file()),
        ..Catalog::default()
    };
    let mut ids = BTreeSet::new();
    for parent in [&active, &staged].into_iter().flatten() {
        ids.extend(parent.names()?);
    }
    for id in ids {
        let mut module = Module {
            name: id.clone(),
            id: id.clone(),
            version: String::new(),
            author: String::new(),
            description: String::new(),
            disabled: false,
            remove: false,
            staged: false,
            problem: None,
        };
        let read = (|| -> Result<()> {
            for (parent, updating) in [(&active, false), (&staged, true)] {
                let Some(parent) = parent else { continue };
                let Some(dir) = optional_child(parent, &id)? else { continue };
                let props = dir.properties().with_context(|| format!("read module {id}"))?;
                ensure!(
                    props.get("id").map(String::as_str) == Some(id.as_str()),
                    "module.prop id differs from directory {id}"
                );
                module.name = props.get("name").cloned().unwrap_or_else(|| id.clone());
                module.version = props.get("version").cloned().unwrap_or_default();
                module.author = props.get("author").cloned().unwrap_or_default();
                module.description = props.get("description").cloned().unwrap_or_default();
                module.disabled |= dir.flag("disable")?;
                module.remove |= dir.flag("remove")?;
                module.staged |= updating || dir.flag("update")?;
            }
            Ok(())
        })();
        if let Err(e) = read {
            module.problem = Some(format!("{e:#}"));
        }
        catalog.modules.push(module);
    }
    Ok(catalog)
}

pub fn change(adb: &Path, id: &str, operation: Operation) -> Result<()> {
    ensure!(valid_id(id), "invalid module id");
    let root = Dir::open(adb)?;
    let mut directories = Vec::new();
    for parent in ["modules", "modules_update"] {
        if let Some(parent) = optional_child(&root, parent)? {
            if let Some(dir) = optional_child(&parent, id)? {
                ensure!(dir.properties()?.get("id").map(String::as_str) == Some(id), "module id changed");
                dir.flag("disable")?;
                let removed = dir.flag("remove")?;
                ensure!(
                    !(operation == Operation::Enable && removed),
                    "cancel pending removal before enabling this module"
                );
                directories.push(dir);
            }
        }
    }
    ensure!(!directories.is_empty(), "module {id} is no longer installed");
    let (marker, present) = match operation {
        Operation::Enable => ("disable", false),
        Operation::Disable => ("disable", true),
        Operation::Remove => ("remove", true),
        Operation::UndoRemove => ("remove", false),
    };
    for dir in directories {
        dir.mark(marker, present)
            .with_context(|| format!("change {marker} for {id}; reread module state before retrying"))?;
    }
    Ok(())
}

/// KernelSU may have copied preinit programs out of data before the next
/// boot. A disabled module must not retain any such runnable directory.
pub fn preinit_present(metadata: &Path, id: &str) -> Result<bool> {
    ensure!(valid_id(id), "invalid module id");
    for base in ["vold/ksu/preinit", "watchdog/ksu/preinit", "ksu/preinit"] {
        match std::fs::symlink_metadata(metadata.join(base).join(id)) {
            Ok(_) => return Ok(true),
            Err(e) if e.kind() == ErrorKind::NotFound => {}
            Err(e) => return Err(e).context("inspect KernelSU cached preinit programs"),
        }
    }
    Ok(false)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::os::unix::fs::symlink;

    fn fixture() -> tempfile::TempDir {
        let tmp = tempfile::tempdir().unwrap();
        let module = tmp.path().join("modules/example");
        fs::create_dir_all(&module).unwrap();
        fs::write(
            module.join("module.prop"),
            "id=example\nname=Test module\nversion=1.0\nauthor=Tester\n",
        )
        .unwrap();
        tmp
    }

    #[test]
    fn enabling_disabling_and_removal_only_change_markers() {
        let tmp = fixture();
        let module = tmp.path().join("modules/example");
        fs::write(module.join("uninstall.sh"), "never run this in recovery").unwrap();
        change(tmp.path(), "example", Operation::Disable).unwrap();
        assert!(scan(tmp.path()).unwrap().modules[0].disabled);
        change(tmp.path(), "example", Operation::Remove).unwrap();
        assert!(scan(tmp.path()).unwrap().modules[0].remove);
        assert!(change(tmp.path(), "example", Operation::Enable).is_err());
        change(tmp.path(), "example", Operation::UndoRemove).unwrap();
        change(tmp.path(), "example", Operation::Enable).unwrap();
        assert!(!scan(tmp.path()).unwrap().modules[0].disabled);
        assert_eq!(
            fs::read_to_string(module.join("uninstall.sh")).unwrap(),
            "never run this in recovery"
        );
    }

    #[test]
    fn active_and_staged_copies_are_both_disabled() {
        let tmp = fixture();
        fs::create_dir_all(tmp.path().join("modules_update/example")).unwrap();
        fs::copy(
            tmp.path().join("modules/example/module.prop"),
            tmp.path().join("modules_update/example/module.prop"),
        )
        .unwrap();
        change(tmp.path(), "example", Operation::Disable).unwrap();
        for parent in ["modules", "modules_update"] {
            assert!(tmp.path().join(parent).join("example/disable").is_file());
        }
        let catalog = scan(tmp.path()).unwrap();
        assert_eq!(catalog.modules.len(), 1);
        assert!(catalog.modules[0].staged);
    }

    #[test]
    fn links_never_redirect_a_module_write() {
        let tmp = fixture();
        let outside = tempfile::tempdir().unwrap();
        fs::write(outside.path().join("target"), b"untouched").unwrap();
        symlink(outside.path().join("target"), tmp.path().join("modules/example/disable")).unwrap();
        assert!(change(tmp.path(), "example", Operation::Disable).is_err());
        assert!(scan(tmp.path()).unwrap().modules[0].problem.is_some());
        assert_eq!(fs::read(outside.path().join("target")).unwrap(), b"untouched");
        for id in ["../target", "", "/example", ".."] {
            assert!(change(tmp.path(), id, Operation::Remove).is_err());
        }
    }

    #[test]
    fn a_staged_symlink_is_rejected_before_the_active_copy_changes() {
        let tmp = fixture();
        fs::create_dir(tmp.path().join("modules_update")).unwrap();
        symlink(tmp.path().join("modules/example"), tmp.path().join("modules_update/example")).unwrap();
        assert!(change(tmp.path(), "example", Operation::Disable).is_err());
        assert!(!tmp.path().join("modules/example/disable").exists());
    }

    #[test]
    fn property_symlinks_and_invalid_properties_stay_visible_as_errors() {
        let tmp = fixture();
        fs::write(tmp.path().join("modules/example/module.prop"), "id=someone_else\n").unwrap();
        assert!(scan(tmp.path()).unwrap().modules[0].problem.is_some());
        assert!(change(tmp.path(), "example", Operation::Remove).is_err());
        fs::remove_file(tmp.path().join("modules/example/module.prop")).unwrap();
        symlink("/dev/null", tmp.path().join("modules/example/module.prop")).unwrap();
        assert!(change(tmp.path(), "example", Operation::Remove).is_err());
    }

    #[test]
    fn a_cached_preinit_copy_is_not_mistaken_for_a_disabled_module() {
        let metadata = tempfile::tempdir().unwrap();
        assert!(!preinit_present(metadata.path(), "example").unwrap());
        fs::create_dir_all(metadata.path().join("vold/ksu/preinit/example")).unwrap();
        assert!(preinit_present(metadata.path(), "example").unwrap());
        assert!(preinit_present(metadata.path(), "../other").is_err());
    }

    #[test]
    fn ordinary_markers_do_not_require_rebuilding_an_empty_cache() {
        let metadata = tempfile::tempdir().unwrap();
        fs::create_dir_all(metadata.path().join("watchdog/ksu")).unwrap();
        fs::write(metadata.path().join("watchdog/ksu/modules.rc"), "\n").unwrap();
        assert!(cache_context(metadata.path()).unwrap().active_roots.is_empty());
        fs::write(metadata.path().join("watchdog/ksu/modules.rc"), "on boot\n").unwrap();
        assert_eq!(
            cache_context(metadata.path()).unwrap().active_roots,
            [metadata.path().join("watchdog/ksu")]
        );
    }

    #[test]
    fn refresh_rejects_an_absent_kernel_driver_or_a_different_layout() {
        let metadata = tempfile::tempdir().unwrap();
        fs::create_dir_all(metadata.path().join("vold/ksu")).unwrap();
        let context = CacheContext {
            active_roots: vec![metadata.path().join("vold/ksu")],
            preinit: true,
        };
        assert!(validate_refresh("version: 0\nuapi_version: 0\nflags: 0x0", metadata.path(), &context, false).is_err());
        assert!(validate_refresh("version: 40000\nuapi_version: 5\nflags: 0x20", metadata.path(), &context, false).is_err());
        assert!(validate_refresh("version: 40000\nuapi_version: 6\nflags: 0x0", metadata.path(), &context, false).is_err());
        validate_refresh("version: 40000\nuapi_version: 6\nflags: 0x20", metadata.path(), &context, false).unwrap();
    }
}
