//! Fork-only on-device fixture probe. Never pass its archive to full-data restore.
#[cfg(not(target_os = "android"))]
fn main() {
    panic!("this probe requires the prepared Android recovery fixture");
}

#[cfg(target_os = "android")]
fn main() -> anyhow::Result<()> {
    android::run()
}

#[cfg(target_os = "android")]
mod android {
    use anyhow::{ensure, Context, Result};
    use skyward_backup::{archive, store};
    use skyward_files::{Clash, Ownership};
    use std::ffi::CString;
    use std::fs::{self, File};
    use std::io::Write;
    use std::os::fd::AsRawFd;
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    use std::path::Path;
    use std::sync::atomic::AtomicBool;

    const ROOT: &str = "/data/media/0/Download/Skyward-RW-test-20261111";
    const MARKER: &[u8] = b"Skyward dedicated RW fixture 20261111; no personal data\n";

    fn attr(path: &Path, name: &str) -> Result<Vec<u8>> {
        let path = CString::new(path.as_os_str().as_encoded_bytes())?;
        let name = CString::new(name)?;
        let size = unsafe { libc::lgetxattr(path.as_ptr(), name.as_ptr(), std::ptr::null_mut(), 0) };
        ensure!(size >= 0, "read fixture attribute: {}", std::io::Error::last_os_error());
        let mut value = vec![0; size as usize];
        let got = unsafe { libc::lgetxattr(path.as_ptr(), name.as_ptr(), value.as_mut_ptr().cast(), value.len()) };
        ensure!(got == size, "fixture attribute changed while reading");
        Ok(value)
    }

    fn set_attr(path: &Path, name: &str, value: &[u8]) -> Result<()> {
        let path = CString::new(path.as_os_str().as_encoded_bytes())?;
        let name = CString::new(name)?;
        let result = unsafe { libc::lsetxattr(path.as_ptr(), name.as_ptr(), value.as_ptr().cast(), value.len(), 0) };
        ensure!(result == 0, "write fixture attribute: {}", std::io::Error::last_os_error());
        Ok(())
    }

    fn policy(path: &Path) -> Result<Vec<u8>> {
        let dir = File::open(path)?;
        let mut value = [0u8; 32];
        value[..8].copy_from_slice(&24u64.to_ne_bytes());
        // _IOWR('f', 22, [u8; 9]), FS_IOC_GET_ENCRYPTION_POLICY_EX.
        let result = unsafe { libc::ioctl(dir.as_raw_fd(), 0xc0096616u32 as libc::c_ulong, value.as_mut_ptr()) };
        ensure!(result == 0, "read fixture encryption policy: {}", std::io::Error::last_os_error());
        let len = u64::from_ne_bytes(value[..8].try_into()?) as usize;
        ensure!(len > 0 && len <= 24, "unexpected encryption policy length");
        Ok(value[8..8 + len].to_vec())
    }

    fn write(path: &Path, bytes: &[u8]) -> Result<()> {
        let mut file = File::options().write(true).create_new(true).open(path)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        Ok(())
    }

    pub fn run() -> Result<()> {
        ensure!(std::env::args_os().count() == 1, "this probe accepts no paths or arguments");
        ensure!(Path::new("/skyward/device.toml").is_file(), "not in the prepared recovery");
        let root = Path::new(ROOT);
        ensure!(root.canonicalize()? == root, "fixture root must not traverse links");
        ensure!(fs::read(root.join("OWNER.txt"))? == MARKER, "fixture owner marker differs");
        let entries = fs::read_dir(root)?.collect::<std::io::Result<Vec<_>>>()?;
        ensure!(entries.len() == 2 && entries.iter().all(|e| matches!(e.file_name().to_str(), Some("OWNER.txt" | "android-canary.bin"))), "fixture is not fresh");
        let expected: Vec<u8> = (0..65536).map(|i| (i % 251) as u8).collect();
        ensure!(fs::read(root.join("android-canary.bin"))? == expected, "Android-created canary differs");
        ensure!(fs::metadata(root)?.dev() == fs::metadata("/data")?.dev(), "fixture is not on userdata");
        let mut stat = std::mem::MaybeUninit::<libc::statvfs>::uninit();
        let croot = CString::new(ROOT)?;
        ensure!(unsafe { libc::statvfs(croot.as_ptr(), stat.as_mut_ptr()) } == 0, "cannot query fixture filesystem");
        ensure!(unsafe { stat.assume_init() }.f_flag & libc::ST_RDONLY as libc::c_ulong == 0, "userdata is read-only");
        let original_policy = policy(root)?;
        ensure!(original_policy[0] == 2, "expected the phone's v2 encryption policy");
        let label = attr(&root.join("android-canary.bin"), "security.selinux")?;
        ensure!(!label.is_empty(), "Android canary has no SELinux label");
        let cancel = AtomicBool::new(false);
        let source = root.join("source");
        let restored = root.join("restored");
        let backup = root.join("backup");
        let copies = root.join("copies");
        for dir in [&source, &restored, &backup, &copies] {
            fs::create_dir(dir)?;
            ensure!(policy(dir)? == original_policy, "new fixture directory did not inherit encryption policy");
        }
        fs::create_dir(source.join("empty"))?;
        for name in ["payload.bin", "Unicode-传输.txt"] {
            write(&source.join(name), &expected)?;
            set_attr(&source.join(name), "security.selinux", &label)?;
            set_attr(&source.join(name), "user.skyward", b"fixture-xattr")?;
            fs::set_permissions(source.join(name), fs::Permissions::from_mode(0o640))?;
        }
        fs::hard_link(source.join("payload.bin"), source.join("hard.bin"))?;
        std::os::unix::fs::symlink("payload.bin", source.join("soft.bin"))?;
        write(&source.join("skipped.bin"), b"not archived")?;
        write(&restored.join("skipped.bin"), b"destination kept")?;
        write(&restored.join("obsolete.bin"), b"fixture-only sweep")?;
        let skipped = archive::Skipped::new(&["skipped.bin".into()]);
        let mut writer = store::Writer::create(&backup, "fixture", true, Ownership::Kept);
        archive::pack(&source, &skipped, &mut writer, &cancel, &mut |_| {})?;
        let stored = writer.finish()?;
        let mut reader = store::Reader::open(&backup, &stored);
        let unpacked = archive::unpack(&mut reader, &restored, &skipped, &cancel, &mut |_| {})?;
        std::io::copy(&mut reader, &mut std::io::sink())?;
        reader.check(&stored)?;
        ensure!(unpacked.unmade.is_empty(), "fixture restore unexpectedly skipped a key root");
        ensure!(fs::read(restored.join("skipped.bin"))? == b"destination kept", "exclusion was not preserved");
        ensure!(!restored.join("obsolete.bin").exists(), "obsolete fixture was not removed");
        ensure!(restored.join("empty").is_dir(), "empty directory was not restored");
        ensure!(fs::read_link(restored.join("soft.bin"))? == Path::new("payload.bin"), "symlink differs");
        ensure!(fs::metadata(restored.join("payload.bin"))?.ino() == fs::metadata(restored.join("hard.bin"))?.ino(), "hardlink differs");
        for name in ["payload.bin", "Unicode-传输.txt"] {
            let from = source.join(name);
            let to = restored.join(name);
            ensure!(fs::read(&to)? == expected, "restored bytes differ");
            let (a, b) = (fs::metadata(&from)?, fs::metadata(&to)?);
            ensure!((a.uid(), a.gid(), a.mode(), a.mtime(), a.mtime_nsec()) == (b.uid(), b.gid(), b.mode(), b.mtime(), b.mtime_nsec()), "restored metadata differs");
            ensure!(attr(&to, "security.selinux")? == label, "restored SELinux label differs");
            ensure!(attr(&to, "user.skyward")? == b"fixture-xattr", "restored xattr differs");
        }
        ensure!(policy(&restored)? == original_policy && policy(&restored.join("empty"))? == original_policy, "restored encryption policy differs");
        skyward_files::copy(&[restored.join("payload.bin")], &copies, Clash::Refuse, Ownership::Kept, &cancel, &mut |_| {})?;
        let renamed = skyward_files::rename(&copies.join("payload.bin"), "renamed.bin")?;
        skyward_files::replace(&renamed, b"fixture edited by production file code")?;
        ensure!(fs::read(&renamed)? == b"fixture edited by production file code", "production edit differs");
        skyward_files::delete(&[renamed], &cancel, &mut |_| {})?;
        ensure!(fs::read_dir(&copies)?.next().is_none(), "production delete left the fixture");
        ensure!(fs::read(root.join("android-canary.bin"))? == expected, "untouched Android canary differs");
        File::open(&restored)?.sync_all().context("sync restored fixture directory")?;
        File::open(root)?.sync_all()?;
        println!("{{\"fixture_probe_passed\":true,\"compressed_archive_sha256\":\"{}\",\"archive_digest_verified\":true,\"payload_bytes\":65536,\"ascii_unicode_contents\":true,\"selinux_owner_mode_times_xattr\":true,\"inherited_fscrypt_v2_policy\":true,\"links_exclusions_and_sweep\":true,\"production_copy_rename_edit_delete\":true,\"android_canary_unchanged\":true}}", stored.sha256);
        Ok(())
    }
}
