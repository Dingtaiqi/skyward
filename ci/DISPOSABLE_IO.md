# Fork-only destructive IO validation

This branch's workflow runs production `erase::make`, `image::flash`, and backup
archive/store code against three fresh loop devices on a GitHub Linux runner.
The binary runs in a private mount namespace and a minimal chroot containing
only the three loop nodes; no phone, real partition aliases, SSH, or USB tools
are exposed. Failure is fatal and never becomes a skipped success.

It checks real ext4/F2FS formatting with native filesystem checkers (the F2FS
check omits repair flags and verifies the entire image remains unchanged), a 64 MiB
block-device write and independent byte comparison, refusal of oversized writes,
and compressed backup/restore on mounted ext4 including hardlinks, symlinks,
permissions, stale-file removal, digest validation and protected exclusions.

Limits: native Ubuntu filesystem tools differ from Android's tool versions.
Android-only `BLKSECDISCARD`/`BLKDISCARD`, SELinux/fscrypt restoration, the
factory-reset orchestration, and a complete OTA/update_engine/VAB/AVB/BCB install
are not executed here. Passing this workflow does not validate those operations
on a K60 Pro or authorize erasing its userdata. The image writer test exercises
its write/readback implementation, not the recovery UI's image classification.

The workflow, integration test, dev-dependencies and branch filter belong to
this fork-only validation branch; they are separate from the device-port PR.
