// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::workspace::{Color, Error, Workspace};

#[test]
fn pin_and_unpin_preserve_prefix_and_session() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let window = w.window().unwrap();
    let a = w.tab(s, window).unwrap();
    let b = w.tab(s, window).unwrap();
    let c = w.tab(s, window).unwrap();
    w.set_pinned(c, true).unwrap();
    w.set_pinned(b, true).unwrap();
    assert_eq!(w.tabs(window).unwrap(), &[c, b, a]);
    w.set_pinned(c, false).unwrap();
    assert_eq!(w.tabs(window).unwrap(), &[b, c, a]);
    assert_eq!(w.pinned(c), Ok(false));
    assert_eq!(w.tab_session(c), Ok(s));
}

#[test]
fn cross_window_moves_respect_pinned_boundary() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let a = w.window().unwrap();
    let b = w.window().unwrap();
    let x = w.tab(s, a).unwrap();
    let y = w.tab(s, b).unwrap();
    let z = w.tab(s, b).unwrap();
    w.set_pinned(x, true).unwrap();
    w.set_pinned(y, true).unwrap();
    assert_eq!(w.move_tab(x, b, 2), Err(Error::Conflict));
    assert_eq!(w.move_tab(z, a, 0), Err(Error::Conflict));
    assert_eq!(w.tabs(a).unwrap(), &[x]);
    assert_eq!(w.tabs(b).unwrap(), &[y, z]);
    w.move_tab(x, b, 1).unwrap();
    assert_eq!(w.tabs(b).unwrap(), &[y, x, z]);
    let separate = w.detach(x).unwrap();
    assert_eq!(w.tabs(separate).unwrap(), &[x]);
    assert_eq!(w.pinned(x), Ok(true));
}

#[test]
fn groups_cannot_cross_pin_boundary() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let a = w.window().unwrap();
    let b = w.window().unwrap();
    let x = w.tab(s, a).unwrap();
    let y = w.tab(s, a).unwrap();
    let z = w.tab(s, b).unwrap();
    w.set_pinned(z, true).unwrap();
    assert_eq!(
        w.create_group(b, &[z], "pin", Color::Blue),
        Err(Error::Conflict)
    );
    let group = w.create_group(a, &[x, y], "team", Color::Blue).unwrap();
    assert_eq!(w.set_pinned(x, true), Err(Error::Conflict));
    assert_eq!(w.move_group(group, b, 0), Err(Error::Conflict));
    w.move_group(group, b, 1).unwrap();
    assert_eq!(w.tabs(b).unwrap(), &[z, x, y]);
}

#[test]
fn reorder_and_remove_member_keep_group_contiguous() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let window = w.window().unwrap();
    let a = w.tab(s, window).unwrap();
    let b = w.tab(s, window).unwrap();
    let c = w.tab(s, window).unwrap();
    let d = w.tab(s, window).unwrap();
    let group = w
        .create_group(window, &[a, b, c], "team", Color::Green)
        .unwrap();
    assert_eq!(w.reorder_member(group, d, 0), Err(Error::Conflict));
    assert_eq!(w.reorder_member(group, a, 3), Err(Error::Conflict));
    w.reorder_member(group, c, 0).unwrap();
    assert_eq!(w.tabs(window).unwrap(), &[c, a, b, d]);
    w.remove_member(group, a).unwrap();
    assert_eq!(w.tabs(window).unwrap(), &[c, b, a, d]);
    assert_eq!(w.group(group).unwrap().tabs, vec![c, b]);
    w.set_pinned(a, true).unwrap();
    assert_eq!(w.tabs(window).unwrap(), &[a, c, b, d]);
    w.remove_member(group, c).unwrap();
    w.remove_member(group, b).unwrap();
    assert_eq!(w.group(group), Err(Error::Unknown));
    assert_eq!(w.tab_session(b), Ok(s));
}

#[test]
fn failed_membership_operations_do_not_change_layout() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let window = w.window().unwrap();
    let a = w.tab(s, window).unwrap();
    let b = w.tab(s, window).unwrap();
    let group = w.create_group(window, &[a], "team", Color::Gray).unwrap();
    assert_eq!(w.remove_member(group, b), Err(Error::Conflict));
    assert_eq!(w.group(group).unwrap().tabs, vec![a]);
    assert_eq!(w.tabs(window).unwrap(), &[a, b]);
    w.remove_member(group, a).unwrap();
    assert_eq!(w.tabs(window).unwrap(), &[a, b]);
}
