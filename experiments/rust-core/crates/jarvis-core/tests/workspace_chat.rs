// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::chat::{AccessMode, Error as ChatError, Submission};
use jarvis_core::workspace::{Color, Error, Workspace};

#[test]
fn moved_group_and_reopened_view_share_exact_pending_chat() {
    let mut w = Workspace::default();
    let p = w.project().unwrap();
    let s = w.session(Some(p)).unwrap();
    let main = w.window().unwrap();
    let first = w.tab(s, main).unwrap();
    let second = w.tab(s, main).unwrap();
    let group = w
        .create_group(main, &[first, second], "Chat", Color::Blue)
        .unwrap();
    let desktop = w.chat_mut(s).unwrap().grant(AccessMode::Write).unwrap();
    let phone = w.chat_mut(s).unwrap().grant(AccessMode::Read).unwrap();
    let Submission::New(turn) = w
        .chat_mut(s)
        .unwrap()
        .submit(&desktop, 0, "request", "Hello")
        .unwrap()
    else {
        panic!("new request expected");
    };
    let detached = w.detach_group(group).unwrap();
    assert_eq!(w.tabs(detached).unwrap(), &[first, second]);
    let same = w.tab_session(second).unwrap();
    assert_eq!(same, s);
    assert_eq!(w.chat(same).unwrap().read(&phone).unwrap().1.len(), 1);
    assert!(matches!(
        w.chat_mut(same)
            .unwrap()
            .submit(&desktop, 0, "request", "Hello"),
        Ok(Submission::Replay(1))
    ));
    w.close_tab(first).unwrap();
    w.close_tab(second).unwrap();
    w.close_empty_window(detached).unwrap();
    w.chat_mut(s).unwrap().complete(&turn, "Done").unwrap();
    let reopened = w.tab(s, main).unwrap();
    assert_eq!(w.tab_session(reopened), Ok(s));
    assert_eq!(w.chat(s).unwrap().read(&phone).unwrap().1.len(), 2);
}

#[test]
fn project_and_session_identifiers_never_confer_chat_access() {
    let mut w = Workspace::default();
    let a = w.project().unwrap();
    let b = w.project().unwrap();
    let sa = w.session(Some(a)).unwrap();
    let sb = w.session(Some(b)).unwrap();
    let sg = w.session(None).unwrap();
    let access = w.chat_mut(sa).unwrap().grant(AccessMode::Write).unwrap();
    for session in [sb, sg] {
        assert_eq!(
            w.chat(session).unwrap().read(&access),
            Err(ChatError::Denied)
        );
        assert_eq!(
            w.chat_mut(session)
                .unwrap()
                .submit(&access, 0, "one", "text")
                .unwrap_err(),
            ChatError::Denied
        );
    }
    assert_eq!(w.session_project(sa), Ok(Some(a)));
    assert_eq!(w.session_project(sb), Ok(Some(b)));
    assert_eq!(w.session_project(sg), Ok(None));
}

#[test]
fn foreign_registry_session_cannot_resolve_to_local_chat() {
    let mut a = Workspace::default();
    let mut b = Workspace::default();
    let sa = a.session(None).unwrap();
    let sb = b.session(None).unwrap();
    assert!(matches!(a.chat(sb), Err(Error::Unknown)));
    assert!(matches!(b.chat_mut(sa), Err(Error::Unknown)));
    assert_eq!(a.chat(sa).unwrap().session(), sa);
    assert_eq!(b.chat(sb).unwrap().session(), sb);
}

#[test]
fn closing_a_window_does_not_clear_cancelled_turn_barrier() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let main = w.window().unwrap();
    let tab = w.tab(s, main).unwrap();
    let access = w.chat_mut(s).unwrap().grant(AccessMode::Write).unwrap();
    let Submission::New(turn) = w
        .chat_mut(s)
        .unwrap()
        .submit(&access, 0, "request", "Hello")
        .unwrap()
    else {
        panic!("new request expected");
    };
    w.chat_mut(s).unwrap().cancel(&access, 1).unwrap();
    w.close_tab(tab).unwrap();
    w.close_empty_window(main).unwrap();
    let second = w.window().unwrap();
    w.tab(s, second).unwrap();
    assert_eq!(
        w.chat_mut(s)
            .unwrap()
            .submit(&access, 2, "next", "New")
            .unwrap_err(),
        ChatError::Busy
    );
    w.chat_mut(s).unwrap().observe_drained(&turn).unwrap();
    assert!(matches!(
        w.chat_mut(s).unwrap().submit(&access, 3, "next", "New"),
        Ok(Submission::New(_))
    ));
}
