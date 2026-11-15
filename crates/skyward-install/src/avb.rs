//! Preparing a current-slot vbmeta change. Writing uses the existing image
//! writer, including its block read-only guard and full hash readback.

use anyhow::{ensure, Result};

/// Use only kernel boot parameters, not mutable Android properties. Some
/// Xiaomi bootloaders omit vbmeta.device_state but report orange instead:
/// https://source.android.com/docs/security/features/verifiedboot/boot-flow
/// Missing, unknown or conflicting evidence must not authorize a write.
pub fn bootloader_unlocked(device_state: Option<&str>, verified_state: Option<&str>) -> bool {
    matches!(
        (device_state, verified_state),
        (Some("unlocked"), None | Some("orange")) | (None, Some("orange"))
    )
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Flags(pub u32);

impl Flags {
    pub fn of(image: &[u8]) -> Result<Self> {
        ensure!(
            image.len() >= 256 && image.starts_with(b"AVB0"),
            "the partition has no top-level AVB header"
        );
        Ok(Self(u32::from_be_bytes(image[120..124].try_into()?)))
    }

    pub fn verity(self) -> bool {
        self.0 & 1 == 0
    }
    pub fn verification(self) -> bool {
        self.0 & 2 == 0
    }
}

/// `checks` chooses verity and verification to disable. Re-enabling an
/// already changed, unsigned vbmeta is deliberately not an automatic action.
pub fn disabled(image: &[u8], expected: Flags, checks: [bool; 2]) -> Result<Vec<u8>> {
    let flags = Flags::of(image)?;
    ensure!(flags == expected, "vbmeta changed since it was shown; read its state again");
    let mask = u32::from(checks[0]) | (u32::from(checks[1]) << 1);
    ensure!(mask != 0 && flags.0 | mask != flags.0, "the selected checks are already disabled");
    let mut changed = image.to_vec();
    changed[120..124].copy_from_slice(&(flags.0 | mask).to_be_bytes());
    Ok(changed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kernel_boot_state_allows_orange_without_a_device_state() {
        assert!(bootloader_unlocked(None, Some("orange")));
        assert!(bootloader_unlocked(Some("unlocked"), Some("orange")));
        assert!(bootloader_unlocked(Some("unlocked"), None));
        for device in [None, Some("locked"), Some("unknown")] {
            for verified in [None, Some("green"), Some("yellow"), Some("red"), Some("unknown")] {
                assert!(!bootloader_unlocked(device, verified));
            }
        }
        assert!(!bootloader_unlocked(Some("locked"), Some("orange")));
        assert!(!bootloader_unlocked(Some("unknown"), Some("orange")));
        assert!(!bootloader_unlocked(Some("unlocked"), Some("green")));
        assert!(!bootloader_unlocked(Some("unlocked"), Some("yellow")));
        assert!(!bootloader_unlocked(Some("unlocked"), Some("unknown")));
    }

    fn image() -> Vec<u8> {
        let mut image = vec![0xa5; 4096];
        image[..4].copy_from_slice(b"AVB0");
        image[120..124].copy_from_slice(&0x40u32.to_be_bytes());
        image
    }

    #[test]
    fn only_the_two_requested_flag_bits_change() {
        let original = image();
        let changed = disabled(&original, Flags(0x40), [true, true]).unwrap();
        assert_eq!(Flags::of(&changed).unwrap(), Flags(0x43));
        assert!(!Flags::of(&changed).unwrap().verity());
        assert!(!Flags::of(&changed).unwrap().verification());
        let mut restored = changed;
        restored[120..124].copy_from_slice(&original[120..124]);
        assert_eq!(restored, original);
    }

    #[test]
    fn stale_headers_and_non_avb_images_are_rejected() {
        assert!(disabled(&image(), Flags(0), [true, true]).is_err());
        assert!(disabled(&[0; 4096], Flags(0), [true, true]).is_err());
        assert!(Flags::of(b"AVB0").is_err());
        assert!(disabled(&image(), Flags(0x40), [false, false]).is_err());
    }

    #[test]
    fn the_production_writer_preserves_every_other_byte() {
        let directory = tempfile::tempdir().unwrap();
        let device = directory.path().join("vbmeta_a");
        let original = image();
        std::fs::write(&device, &original).unwrap();
        let changed = disabled(&original, Flags(0x40), [true, true]).unwrap();
        crate::image::flash(
            &mut std::io::Cursor::new(&changed),
            changed.len() as u64,
            std::slice::from_ref(&device),
            &mut |_, _| {},
        )
        .unwrap();
        assert_eq!(std::fs::read(&device).unwrap(), changed);
        assert!(!directory.path().join("vbmeta_b").exists());
    }

    #[test]
    fn each_check_can_be_disabled_without_touching_the_other() {
        for (checks, bits) in [([true, false], 1), ([false, true], 2)] {
            let changed = disabled(&image(), Flags(0x40), checks).unwrap();
            assert_eq!(Flags::of(&changed).unwrap().0, 0x40 | bits);
            assert!(disabled(&changed, Flags(0x40 | bits), checks).is_err());
        }
    }
}
