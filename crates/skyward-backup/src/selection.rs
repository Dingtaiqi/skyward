//! An explicit selection of partition images, alongside the existing
//! grouped backup and the data partition's file archive.

use crate::Part;
use anyhow::{ensure, Result};
use std::collections::BTreeSet;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Partition {
    pub name: String,
    pub part: Part,
    /// `None` for the data file archive, measured when backing up starts.
    pub bytes: Option<u64>,
}

pub fn valid_name(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 128
        && name != "."
        && name != ".."
        && name.bytes().all(|b| b.is_ascii_alphanumeric() || b"_-.".contains(&b))
}

pub fn selected(names: &[String], available: &[Partition]) -> Result<[bool; 4]> {
    ensure!(!names.is_empty(), "no partition selected");
    let mut seen = BTreeSet::new();
    let mut groups = [false; 4];
    for name in names {
        ensure!(valid_name(name) && seen.insert(name), "invalid or repeated partition {name}");
        let partition = available
            .iter()
            .find(|p| p.name == *name)
            .ok_or_else(|| anyhow::anyhow!("partition {name} is no longer available"))?;
        groups[partition.part.index()] = true;
    }
    Ok(groups)
}

pub fn mask(names: &[String], available: &[Partition]) -> Result<Vec<bool>> {
    selected(names, available)?;
    Ok(available.iter().map(|p| names.contains(&p.name)).collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_single_partition_does_not_select_its_whole_group() {
        let available = [Partition {
            name: "init_boot".into(),
            part: Part::Boot,
            bytes: Some(8388608),
        }];
        assert_eq!(selected(&["init_boot".into()], &available).unwrap(), [true, false, false, false]);
        assert!(selected(&["../boot".into()], &available).is_err());
        assert!(selected(&["init_boot".into(), "init_boot".into()], &available).is_err());
        assert!(selected(&["vendor".into()], &available).is_err());
        assert!(selected(&[], &available).is_err());
    }

    #[test]
    fn an_explicit_selection_leaves_other_images_of_the_same_group_out() {
        let available = ["boot", "init_boot", "vendor_boot"].map(|name| Partition {
            name: name.into(),
            part: Part::Boot,
            bytes: Some(4096),
        });
        assert_eq!(mask(&["init_boot".into()], &available).unwrap(), [false, true, false]);
        assert_eq!(
            mask(&["boot".into(), "vendor_boot".into()], &available).unwrap(),
            [true, false, true]
        );
    }
}
