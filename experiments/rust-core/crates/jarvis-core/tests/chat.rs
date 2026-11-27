// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::chat::{AccessMode, Chat, Error, Role, Submission, Turn};
use jarvis_core::workspace::{SessionId, Workspace};
use std::ops::{Deref, DerefMut};

struct Fixture {
    workspace: Workspace,
    session: SessionId,
}

impl Deref for Fixture {
    type Target = Chat;

    fn deref(&self) -> &Self::Target {
        self.workspace.chat(self.session).unwrap()
    }
}

impl DerefMut for Fixture {
    fn deref_mut(&mut self) -> &mut Self::Target {
        self.workspace.chat_mut(self.session).unwrap()
    }
}

fn fixture() -> Fixture {
    let mut workspace = Workspace::default();
    let session = workspace.session(None).unwrap();
    Fixture { workspace, session }
}

fn ticket(submission: Submission) -> Turn {
    match submission {
        Submission::New(turn) => turn,
        Submission::Replay(_) => panic!("expected a new turn"),
    }
}

#[test]
fn same_chat_across_two_clients_and_retry_is_idempotent() {
    let mut chat = fixture();
    let desktop = chat.grant(AccessMode::Write).unwrap();
    let phone = chat.grant(AccessMode::Write).unwrap();
    let turn = ticket(chat.submit(&desktop, 0, "first", "Hello").unwrap());
    assert!(matches!(
        chat.submit(&desktop, 0, "first", "Hello"),
        Ok(Submission::Replay(1))
    ));
    assert_eq!(chat.read(&phone).unwrap().1[0].text, "Hello");
    assert_eq!(
        chat.submit(&phone, 0, "next", "Hi").unwrap_err(),
        Error::Conflict
    );
    assert_eq!(
        chat.submit(&phone, 1, "next", "Hi").unwrap_err(),
        Error::Busy
    );
    chat.complete(&turn, "Welcome").unwrap();
    let (revision, messages) = chat.read(&phone).unwrap();
    assert_eq!(revision, 2);
    assert_eq!(messages.len(), 2);
    assert_eq!(messages[1].role, Role::Assistant);
    assert!(matches!(
        chat.submit(&desktop, 0, "first", "Hello"),
        Ok(Submission::Replay(1))
    ));
    assert_eq!(chat.read(&phone).unwrap().1.len(), 2);
    assert!(matches!(
        chat.submit(&phone, 2, "next", "Hi"),
        Ok(Submission::New(_))
    ));
}

#[test]
fn same_request_key_different_content_is_conflict() {
    let mut chat = fixture();
    let user = chat.grant(AccessMode::Write).unwrap();
    let turn = ticket(chat.submit(&user, 0, "one", "original").unwrap());
    chat.complete(&turn, "ok").unwrap();
    assert_eq!(
        chat.submit(&user, 2, "one", "changed").unwrap_err(),
        Error::Conflict
    );
    assert_eq!(chat.read(&user).unwrap().1.len(), 2);
}

#[test]
fn read_only_and_foreign_handles_cannot_submit() {
    let mut chat = fixture();
    let read = chat.grant(AccessMode::Read).unwrap();
    let mut other = fixture();
    let foreign = other.grant(AccessMode::Write).unwrap();
    assert_eq!(chat.read(&foreign), Err(Error::Denied));
    assert_eq!(
        chat.submit(&read, 0, "one", "text").unwrap_err(),
        Error::Denied
    );
    assert_eq!(
        chat.submit(&foreign, 0, "one", "text").unwrap_err(),
        Error::Denied
    );
    assert_eq!(chat.cancel(&read, 0), Err(Error::Denied));
    assert_eq!(chat.read(&read).unwrap().0, 0);
}

#[test]
fn revoked_writer_cannot_publish_late_or_read_history() {
    let mut chat = fixture();
    let writer = chat.grant(AccessMode::Write).unwrap();
    let reader = chat.grant(AccessMode::Read).unwrap();
    let turn = ticket(chat.submit(&writer, 0, "one", "text").unwrap());
    chat.revoke(&writer).unwrap();
    assert_eq!(chat.read(&writer), Err(Error::Denied));
    assert_eq!(chat.complete(&turn, "late"), Err(Error::Conflict));
    assert_eq!(chat.read(&reader).unwrap().0, 2);
    assert_eq!(chat.archive(), Err(Error::Busy));
    chat.observe_drained(&turn).unwrap();
    assert_eq!(chat.read(&reader).unwrap().0, 3);
    assert_eq!(chat.read(&reader).unwrap().1.len(), 1);
}

#[test]
fn cancellation_preserves_user_message_and_blocks_late_result() {
    let mut chat = fixture();
    let user = chat.grant(AccessMode::Write).unwrap();
    let old = ticket(chat.submit(&user, 0, "one", "text").unwrap());
    assert_eq!(chat.cancel(&user, 0), Err(Error::Conflict));
    chat.cancel(&user, 1).unwrap();
    assert_eq!(chat.complete(&old, "late"), Err(Error::Conflict));
    assert!(matches!(
        chat.submit(&user, 0, "one", "text"),
        Ok(Submission::Replay(1))
    ));
    assert_eq!(
        chat.submit(&user, 2, "two", "again").unwrap_err(),
        Error::Busy
    );
    chat.observe_drained(&old).unwrap();
    let next = ticket(chat.submit(&user, 3, "two", "again").unwrap());
    assert_eq!(chat.complete(&old, "late"), Err(Error::Foreign));
    chat.complete(&next, "ok").unwrap();
    assert_eq!(chat.read(&user).unwrap().1.len(), 3);
}

#[test]
fn archive_requires_idle_and_retains_history() {
    let mut chat = fixture();
    let user = chat.grant(AccessMode::Write).unwrap();
    let turn = ticket(chat.submit(&user, 0, "one", "text").unwrap());
    assert_eq!(chat.archive(), Err(Error::Busy));
    chat.complete(&turn, "ok").unwrap();
    chat.archive().unwrap();
    assert_eq!(chat.read(&user).unwrap().1.len(), 2);
    assert_eq!(
        chat.submit(&user, 3, "two", "new").unwrap_err(),
        Error::Archived
    );
    chat.archive().unwrap();
    assert_eq!(chat.read(&user).unwrap().0, 3);
}

#[test]
fn invalid_or_oversized_text_and_keys_are_non_mutating() {
    let mut chat = fixture();
    let user = chat.grant(AccessMode::Write).unwrap();
    for (key, text) in [("", "ok"), ("bad/key", "ok"), ("ok", " "), ("ok", "x\0y")] {
        assert_eq!(
            chat.submit(&user, 0, key, text).unwrap_err(),
            Error::Invalid
        );
    }
    assert_eq!(
        chat.submit(&user, 0, "one", &"x".repeat(16_385))
            .unwrap_err(),
        Error::Invalid
    );
    let turn = ticket(chat.submit(&user, 0, "one", "valid").unwrap());
    assert_eq!(chat.complete(&turn, ""), Err(Error::Invalid));
    chat.complete(&turn, "ok").unwrap();
    assert_eq!(chat.read(&user).unwrap().1.len(), 2);
}

#[test]
fn foreign_turn_and_replayed_completion_are_denied() {
    let mut a = fixture();
    let mut b = fixture();
    let ga = a.grant(AccessMode::Write).unwrap();
    let gb = b.grant(AccessMode::Write).unwrap();
    let ta = ticket(a.submit(&ga, 0, "one", "text").unwrap());
    let tb = ticket(b.submit(&gb, 0, "one", "text").unwrap());
    assert_eq!(a.complete(&tb, "wrong"), Err(Error::Foreign));
    a.complete(&ta, "correct").unwrap();
    assert_eq!(a.complete(&ta, "duplicate"), Err(Error::Foreign));
    assert_eq!(b.read(&gb).unwrap().1.len(), 1);
}

#[test]
fn storage_and_grant_lifetime_are_bounded() {
    let mut chat = fixture();
    let user = chat.grant(AccessMode::Write).unwrap();
    for i in 0..512 {
        let key = format!("request-{i}");
        let turn = ticket(chat.submit(&user, 2 * i, &key, "x").unwrap());
        chat.complete(&turn, "y").unwrap();
    }
    assert_eq!(chat.read(&user).unwrap().1.len(), 1024);
    assert_eq!(
        chat.submit(&user, 1024, "overflow", "x").unwrap_err(),
        Error::Limit
    );
    for _ in 1..64 {
        let handle = chat.grant(AccessMode::Read).unwrap();
        chat.revoke(&handle).unwrap();
    }
    assert_eq!(chat.grant(AccessMode::Read).unwrap_err(), Error::Limit);
}

#[test]
fn cancelled_turn_requires_exact_drain_evidence() {
    let mut chat = fixture();
    let user = chat.grant(AccessMode::Write).unwrap();
    let turn = ticket(chat.submit(&user, 0, "one", "text").unwrap());
    assert_eq!(chat.observe_drained(&turn), Err(Error::Conflict));
    chat.cancel(&user, 1).unwrap();
    assert_eq!(chat.cancel(&user, 2), Err(Error::Conflict));
    let mut other = fixture();
    let foreign = other.grant(AccessMode::Write).unwrap();
    let wrong = ticket(other.submit(&foreign, 0, "one", "text").unwrap());
    assert_eq!(chat.observe_drained(&wrong), Err(Error::Foreign));
    assert_eq!(
        chat.submit(&user, 2, "two", "next").unwrap_err(),
        Error::Busy
    );
    chat.observe_drained(&turn).unwrap();
    assert_eq!(chat.observe_drained(&turn), Err(Error::Foreign));
    assert_eq!(chat.read(&user).unwrap().0, 3);
    chat.archive().unwrap();
}
