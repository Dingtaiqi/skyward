//! Fork-only integration test. It refuses to run outside the disposable CI jail.
#![cfg(target_os = "linux")]

use skyward_backup::{archive, store};
use skyward_files::Ownership;
use std::fs::{self, File};
use std::io::{self, Cursor};
use std::os::unix::fs::{symlink, FileTypeExt, MetadataExt, PermissionsExt};
use std::path::Path;
use std::process::Command;
use std::sync::atomic::AtomicBool;

const SOURCE: &str = "/dev/test-source";
const TARGET: &str = "/dev/test-target";
const F2FS: &str = "/dev/test-f2fs";
const EXT4_BYTES: u64 = 64 << 20;

fn run(program: &str, arguments: &[&str]) {
    let out = Command::new(program).args(arguments).output().unwrap();
    println!(
        "{program} {arguments:?}\n{}{}",
        String::from_utf8_lossy(&out.stdout),
        String::from_utf8_lossy(&out.stderr)
    );
    assert!(out.status.success(), "{program}: {}", out.status);
}

fn assert_jail() {
    assert_eq!(
        fs::read_to_string("/test/disposable-only").unwrap(),
        "skyward-ci-loopback-v1\n"
    );
    assert_eq!(std::env::var("GITHUB_ACTIONS").unwrap(), "true");
    assert!(!Path::new("/dev/block/by-name").exists());
    assert!(!Path::new("/home/runner").exists());
    for device in [SOURCE, TARGET, F2FS] {
        let meta = fs::symlink_metadata(device).unwrap();
        assert!(meta.file_type().is_block_device());
        assert_eq!(
            libc::major(meta.rdev()),
            7,
            "only the three loop device nodes exist in this jail"
        );
    }
}

#[test]
#[ignore = "requires ci/disposable-io.sh's private namespace, jail and disposable loop devices"]
fn disposable_loopbacks_round_trip() {
    assert_jail();
    // These are the real production formatting entrypoints and native
    // filesystem utilities, not test replacements that merely log arguments.
    skyward_block::erase::make(Path::new(SOURCE), "ext4", &["metadata_csum".into()], false)
        .unwrap();
    run("/system/bin/e2fsck", &["-f", "-n", SOURCE]);
    skyward_block::erase::make(Path::new(F2FS), "f2fs", &[], true).unwrap();
    run("/system/bin/fsck.f2fs", &["-f", F2FS]);
    println!("PASS real ext4 and f2fs formatting and independent filesystem checks");

    run("/bin/mount", &["-t", "ext4", SOURCE, "/fixture/source"]);
    let from = Path::new("/fixture/source");
    fs::create_dir_all(from.join("app/files")).unwrap();
    let payload: Vec<u8> = (0..3_000_017).map(|i| (i % 251) as u8).collect();
    fs::write(from.join("app/files/fixture.bin"), &payload).unwrap();
    fs::set_permissions(
        from.join("app/files/fixture.bin"),
        fs::Permissions::from_mode(0o640),
    )
    .unwrap();
    fs::hard_link(
        from.join("app/files/fixture.bin"),
        from.join("app/second-name"),
    )
    .unwrap();
    symlink("files/fixture.bin", from.join("app/symbolic-name")).unwrap();
    fs::create_dir_all(from.join("media/0")).unwrap();
    fs::write(
        from.join("media/0/excluded"),
        b"do not archive internal storage",
    )
    .unwrap();
    fs::create_dir_all(from.join("system_de/0/spblob")).unwrap();
    fs::write(
        from.join("system_de/0/spblob/excluded"),
        b"synthetic credential state, not a real key",
    )
    .unwrap();
    run("/bin/umount", &["/fixture/source"]);

    // Use a real block target and the unmodified production write/readback
    // path, then flush Linux's buffers and compare both devices byte by byte
    // with an independent utility before mounting what was written.
    let mut source = File::open(SOURCE).unwrap();
    let mut progress = Vec::new();
    skyward_install::image::flash(&mut source, EXT4_BYTES, &[TARGET.into()], &mut |n, all| {
        progress.push((n, all))
    })
    .unwrap();
    assert_eq!(
        progress.last().copied(),
        Some((2 * EXT4_BYTES, 2 * EXT4_BYTES))
    );
    run("/sbin/blockdev", &["--flushbufs", TARGET]);
    run("/usr/bin/cmp", &[SOURCE, TARGET]);
    let refusal = skyward_install::image::flash(
        &mut Cursor::new(vec![0]),
        EXT4_BYTES + 1,
        &[TARGET.into()],
        &mut |_, _| {},
    );
    assert!(
        refusal.is_err(),
        "oversized images must be rejected before writes"
    );
    run("/usr/bin/cmp", &[SOURCE, TARGET]);
    run("/system/bin/e2fsck", &["-f", "-n", TARGET]);
    println!("PASS production image writer, independent whole-device comparison and pre-write oversize refusal");

    run("/bin/mount", &["-t", "ext4", SOURCE, "/fixture/source"]);
    run("/bin/mount", &["-t", "ext4", TARGET, "/fixture/target"]);
    let to = Path::new("/fixture/target");
    assert_eq!(fs::read(to.join("app/files/fixture.bin")).unwrap(), payload);
    let skipped = archive::Skipped::new(&[
        "media".into(),
        "system_de/*/spblob".into(),
        "lost+found".into(),
    ]);
    let cancel = AtomicBool::new(false);
    let backup = Path::new("/fixture/backup");
    let mut writer = store::Writer::create(backup, "data", true, Ownership::Kept);
    archive::pack(from, &skipped, &mut writer, &cancel, &mut |_| {}).unwrap();
    let stored = writer.finish().unwrap();
    fs::write(to.join("app/files/fixture.bin"), b"modified after backup").unwrap();
    fs::write(to.join("app/stale"), b"must be removed by restore").unwrap();
    fs::write(to.join("media/0/excluded"), b"current media stays").unwrap();
    fs::write(
        to.join("system_de/0/spblob/excluded"),
        b"current credential state stays",
    )
    .unwrap();
    let mut reader = store::Reader::open(backup, &stored);
    let restored = archive::unpack(&mut reader, to, &skipped, &cancel, &mut |_| {}).unwrap();
    io::copy(&mut reader, &mut io::sink()).unwrap();
    reader.check(&stored).unwrap();
    assert_eq!(restored, archive::Unpacked::default());
    assert_eq!(fs::read(to.join("app/files/fixture.bin")).unwrap(), payload);
    assert_eq!(
        fs::metadata(to.join("app/files/fixture.bin"))
            .unwrap()
            .mode()
            & 0o7777,
        0o640
    );
    assert_eq!(
        fs::metadata(to.join("app/files/fixture.bin"))
            .unwrap()
            .ino(),
        fs::metadata(to.join("app/second-name")).unwrap().ino()
    );
    assert_eq!(
        fs::read_link(to.join("app/symbolic-name")).unwrap(),
        Path::new("files/fixture.bin")
    );
    assert!(!to.join("app/stale").exists());
    assert_eq!(
        fs::read(to.join("media/0/excluded")).unwrap(),
        b"current media stays"
    );
    assert_eq!(
        fs::read(to.join("system_de/0/spblob/excluded")).unwrap(),
        b"current credential state stays"
    );
    println!("PASS compressed backup, digest verification, real ext4 restore, link/permission retention and protected exclusions");
    run("/bin/umount", &["/fixture/source"]);
    run("/bin/umount", &["/fixture/target"]);
    run("/system/bin/e2fsck", &["-f", "-n", TARGET]);
}
