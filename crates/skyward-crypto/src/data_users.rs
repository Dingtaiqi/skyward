//! Keep a secondary user's failure local to that user. The owner's files
//! must open normally before internal storage is offered to the recovery.

use anyhow::{Context, Result};

pub(crate) const OWNER: u32 = 0;

/// Files still closed. An unknown lock is unavailable, never an invitation
/// to guess a credential or treat a missing protector as no screen lock.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Closed {
    pub user: u32,
    pub lock: Option<i32>,
    pub made_up: bool,
    pub why: Option<String>,
}

pub(crate) enum User<N> {
    Open(N),
    Locked { lock: i32, made_up: bool },
}

fn unavailable(user: u32, error: anyhow::Error) -> Closed {
    Closed {
        user,
        lock: None,
        made_up: false,
        why: Some(format!("{error:#}")),
    }
}

/// All DE keys are attempted before reading protectors: the owner's DE
/// key also opens the locksettings database. A failed secondary DE key is
/// never followed by a protector read or a secure-world credential check.
pub(crate) fn open<N: Default>(
    mut users: Vec<u32>,
    mut device_key: impl FnMut(u32) -> Result<()>,
    mut files: impl FnMut(u32) -> Result<User<N>>,
) -> Result<(Vec<Closed>, N)> {
    anyhow::ensure!(users.contains(&OWNER), "the phone keeps no DE key for owner user 0");
    users.sort_unstable();
    users.dedup();
    let mut ready = Vec::new();
    let mut closed = Vec::new();
    for user in users {
        match device_key(user).with_context(|| format!("open DE files of user {user}")) {
            Ok(()) => ready.push(user),
            Err(e) if user == OWNER => return Err(e),
            Err(e) => closed.push(unavailable(user, e)),
        }
    }
    let mut owner = N::default();
    for user in ready {
        match files(user).with_context(|| format!("read or open CE files of user {user}")) {
            Ok(User::Open(note)) if user == OWNER => owner = note,
            Ok(User::Open(_)) => {}
            Ok(User::Locked { lock, made_up }) => closed.push(Closed {
                user,
                lock: Some(lock),
                made_up,
                why: None,
            }),
            Err(e) if user == OWNER => return Err(e),
            Err(e) => closed.push(unavailable(user, e)),
        }
    }
    closed.sort_by_key(|c| c.user);
    Ok((closed, owner))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_missing_secondary_spblob_does_not_hide_the_owner() {
        let (closed, owner) = open(
            vec![10, 0, 11],
            |_| Ok(()),
            |user| match user {
                0 => Ok(User::Open(7u32)),
                10 => Err(std::io::Error::from(std::io::ErrorKind::NotFound)).context("read /data/system_de/10/spblob"),
                _ => Ok(User::Locked { lock: 3, made_up: false }),
            },
        )
        .unwrap();
        assert_eq!(owner, 7);
        assert_eq!(
            closed.iter().map(|c| (c.user, c.lock)).collect::<Vec<_>>(),
            [(10, None), (11, Some(3))]
        );
        assert!(closed[0].why.as_ref().unwrap().contains("/data/system_de/10/spblob"));
        assert!(!closed[0].made_up);
    }

    #[test]
    fn an_unavailable_profile_does_not_replace_the_owners_pin() {
        let (closed, _) = open::<()>(
            vec![10, 0],
            |_| Ok(()),
            |user| match user {
                0 => Ok(User::Locked { lock: 3, made_up: false }),
                _ => anyhow::bail!("missing spblob"),
            },
        )
        .unwrap();
        assert_eq!((closed[0].user, closed[0].lock), (0, Some(3)));
        assert_eq!(closed[1].lock, None);
    }

    #[test]
    fn a_secondary_de_failure_never_attempts_its_protector() {
        let mut attempted = Vec::new();
        let (closed, _) = open(
            vec![0, 10],
            |user| {
                anyhow::ensure!(user == 0, "unusable DE key");
                Ok(())
            },
            |user| {
                attempted.push(user);
                Ok(User::Open(()))
            },
        )
        .unwrap();
        assert_eq!(attempted, [0]);
        assert_eq!(closed[0].lock, None);
        assert!(closed[0].why.as_ref().unwrap().contains("unusable DE key"));
    }

    #[test]
    fn owner_errors_still_fail_the_opening() {
        let e = open::<()>(vec![0, 10], |_| anyhow::bail!("DE unavailable"), |_| panic!("no CE attempt")).unwrap_err();
        assert!(format!("{e:#}").contains("DE unavailable"));
        let e = open::<()>(vec![0, 10], |_| Ok(()), |_| anyhow::bail!("owner protector missing")).unwrap_err();
        assert!(format!("{e:#}").contains("owner protector missing"));
    }

    #[test]
    fn no_owner_is_not_a_successful_decryption() {
        assert!(open::<()>(vec![10], |_| panic!("no key attempt"), |_| panic!("no CE attempt")).is_err());
    }

    #[test]
    fn a_secondary_ce_failure_is_retained_as_unavailable() {
        let (closed, _) = open(
            vec![0, 10],
            |_| Ok(()),
            |user| match user {
                0 => Ok(User::Open(())),
                _ => anyhow::bail!("malformed protector or unusable CE key"),
            },
        )
        .unwrap();
        assert_eq!(closed[0].lock, None);
        assert!(closed[0].why.is_some());
    }

    #[test]
    fn successful_and_owner_tied_users_keep_their_normal_states() {
        let (closed, owner) = open(
            vec![11, 0, 10],
            |_| Ok(()),
            |user| match user {
                0 => Ok(User::Open(7u32)),
                10 => Ok(User::Open(8)),
                _ => Ok(User::Locked { lock: 3, made_up: true }),
            },
        )
        .unwrap();
        assert_eq!(owner, 7);
        assert_eq!(
            closed,
            [Closed {
                user: 11,
                lock: Some(3),
                made_up: true,
                why: None
            }]
        );
    }
}
