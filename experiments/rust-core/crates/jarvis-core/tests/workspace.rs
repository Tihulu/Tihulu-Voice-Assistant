// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::workspace::{Error, Workspace};

#[test]
fn detach_and_rejoin_preserve_session() {
    let mut w = Workspace::default();
    let p = w.project().unwrap();
    let s = w.session(Some(p)).unwrap();
    let first = w.window().unwrap();
    let a = w.tab(s, first).unwrap();
    let b = w.tab(s, first).unwrap();
    let second = w.detach(b).unwrap();
    assert_eq!(w.tabs(first).unwrap(), &[a]);
    assert_eq!(w.tabs(second).unwrap(), &[b]);
    assert_eq!(w.tab_session(b), Ok(s));
    w.move_tab(b, first, 0).unwrap();
    assert_eq!(w.tabs(first).unwrap(), &[b, a]);
    w.close_empty_window(second).unwrap();
    assert_eq!(w.session_project(s), Ok(Some(p)));
}

#[test]
fn invalid_move_is_non_destructive() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let first = w.window().unwrap();
    let second = w.window().unwrap();
    let a = w.tab(s, first).unwrap();
    assert_eq!(w.move_tab(a, second, 1), Err(Error::Conflict));
    assert_eq!(w.move_tab(a, first, 1), Err(Error::Conflict));
    assert_eq!(w.tabs(first).unwrap(), &[a]);
    assert!(w.tabs(second).unwrap().is_empty());
}

#[test]
fn close_view_preserves_session() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let first = w.window().unwrap();
    let a = w.tab(s, first).unwrap();
    assert_eq!(w.close_empty_window(first), Err(Error::Conflict));
    w.close_tab(a).unwrap();
    w.close_empty_window(first).unwrap();
    assert_eq!(w.session_project(s), Ok(None));
    let second = w.window().unwrap();
    let b = w.tab(s, second).unwrap();
    assert_ne!(a, b);
    assert_eq!(w.tab_session(a), Err(Error::Unknown));
    assert_eq!(w.tab_session(b), Ok(s));
}

#[test]
fn foreign_ids_are_not_local_objects() {
    let mut a = Workspace::default();
    let mut b = Workspace::default();
    let pa = a.project().unwrap();
    let pb = b.project().unwrap();
    assert_ne!(pa, pb);
    assert_eq!(a.session(Some(pb)), Err(Error::Unknown));
    let sa = a.session(Some(pa)).unwrap();
    let sb = b.session(Some(pb)).unwrap();
    let wa = a.window().unwrap();
    let wb = b.window().unwrap();
    let ta = a.tab(sa, wa).unwrap();
    let tb = b.tab(sb, wb).unwrap();
    assert_eq!(a.tab(sb, wa), Err(Error::Unknown));
    assert_eq!(a.tab(sa, wb), Err(Error::Unknown));
    assert_eq!(a.move_tab(ta, wb, 0), Err(Error::Unknown));
    assert_eq!(a.close_tab(tb), Err(Error::Unknown));
    assert_eq!(a.tabs(wa).unwrap(), &[ta]);
}

#[test]
fn lifetime_limit_does_not_move_tab() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let window = w.window().unwrap();
    let tab = w.tab(s, window).unwrap();
    for _ in 3..1024 {
        let extra = w.window().unwrap();
        w.close_empty_window(extra).unwrap();
    }
    assert_eq!(w.detach(tab), Err(Error::Limit));
    assert_eq!(w.tabs(window).unwrap(), &[tab]);
    assert_eq!(w.tab_session(tab), Ok(s));
}
