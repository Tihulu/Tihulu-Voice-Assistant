// SPDX-License-Identifier: AGPL-3.0-only
use jarvis_core::workspace::{Color, Error, Workspace};

#[test]
fn group_detach_rejoin_preserves_sessions_and_metadata() {
    let mut w = Workspace::default();
    let p = w.project().unwrap();
    let a = w.session(Some(p)).unwrap();
    let b = w.session(None).unwrap();
    let main = w.window().unwrap();
    let first = w.tab(a, main).unwrap();
    let second = w.tab(b, main).unwrap();
    let other = w.tab(a, main).unwrap();
    let group = w
        .create_group(main, &[first, second], "Coding", Color::Blue)
        .unwrap();
    w.edit_group(group, "Review", Color::Purple, true).unwrap();
    let separate = w.detach_group(group).unwrap();
    assert_eq!(w.tabs(main).unwrap(), &[other]);
    assert_eq!(w.tabs(separate).unwrap(), &[first, second]);
    w.move_group(group, main, 1).unwrap();
    assert_eq!(w.tabs(main).unwrap(), &[other, first, second]);
    let row = w.group(group).unwrap();
    assert_eq!(row.name, "Review");
    assert_eq!(row.color, Color::Purple);
    assert!(row.collapsed);
    assert_eq!(row.window, main);
    assert_eq!(w.tab_session(first), Ok(a));
    assert_eq!(w.tab_session(second), Ok(b));
    w.close_empty_window(separate).unwrap();
}

#[test]
fn discontiguous_duplicate_and_overlapping_groups_are_rejected() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let window = w.window().unwrap();
    let a = w.tab(s, window).unwrap();
    let b = w.tab(s, window).unwrap();
    let c = w.tab(s, window).unwrap();
    assert_eq!(
        w.create_group(window, &[a, c], "gap", Color::Blue),
        Err(Error::Conflict)
    );
    assert_eq!(
        w.create_group(window, &[b, b], "duplicate", Color::Blue),
        Err(Error::Conflict)
    );
    let group = w
        .create_group(window, &[a, b], "valid", Color::Blue)
        .unwrap();
    assert_eq!(
        w.create_group(window, &[b, c], "overlap", Color::Green),
        Err(Error::Conflict)
    );
    assert_eq!(w.group(group).unwrap().tabs, vec![a, b]);
    assert_eq!(w.tabs(window).unwrap(), &[a, b, c]);
}

#[test]
fn cannot_split_groups_or_move_grouped_tab_implicitly() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let a = w.window().unwrap();
    let b = w.window().unwrap();
    let first = w.tab(s, a).unwrap();
    let second = w.tab(s, a).unwrap();
    let free = w.tab(s, b).unwrap();
    let group = w
        .create_group(a, &[first, second], "team", Color::Green)
        .unwrap();
    assert_eq!(w.move_tab(free, a, 1), Err(Error::Conflict));
    assert_eq!(w.move_tab(first, b, 1), Err(Error::Conflict));
    assert_eq!(w.detach(second), Err(Error::Conflict));
    assert_eq!(w.tabs(a).unwrap(), &[first, second]);
    assert_eq!(w.tabs(b).unwrap(), &[free]);
    w.ungroup(group).unwrap();
    w.move_tab(first, b, 1).unwrap();
    assert_eq!(w.tabs(b).unwrap(), &[free, first]);
}

#[test]
fn group_cannot_be_inserted_inside_another_group() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let a = w.window().unwrap();
    let b = w.window().unwrap();
    let x = w.tab(s, a).unwrap();
    let y = w.tab(s, a).unwrap();
    let z = w.tab(s, b).unwrap();
    let group = w.create_group(a, &[x, y], "a", Color::Gray).unwrap();
    let other = w.create_group(b, &[z], "b", Color::Gray).unwrap();
    assert_eq!(w.move_group(other, a, 1), Err(Error::Conflict));
    assert_eq!(w.move_group(group, b, 2), Err(Error::Conflict));
    assert_eq!(w.tabs(a).unwrap(), &[x, y]);
    assert_eq!(w.tabs(b).unwrap(), &[z]);
    w.move_group(other, a, 2).unwrap();
    assert_eq!(w.tabs(a).unwrap(), &[x, y, z]);
    w.move_group(group, a, 1).unwrap();
    assert_eq!(w.tabs(a).unwrap(), &[z, x, y]);
}

#[test]
fn closing_group_members_never_deletes_sessions() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let a = w.window().unwrap();
    let x = w.tab(s, a).unwrap();
    let y = w.tab(s, a).unwrap();
    let group = w.create_group(a, &[x, y], "team", Color::Orange).unwrap();
    w.close_tab(x).unwrap();
    assert_eq!(w.group(group).unwrap().tabs, vec![y]);
    w.close_tab(y).unwrap();
    assert_eq!(w.group(group), Err(Error::Unknown));
    assert_eq!(w.session_project(s), Ok(None));
    w.close_empty_window(a).unwrap();
}

#[test]
fn invalid_names_and_foreign_groups_do_not_mutate() {
    let mut w = Workspace::default();
    let s = w.session(None).unwrap();
    let a = w.window().unwrap();
    let x = w.tab(s, a).unwrap();
    for name in ["", "  ", "bad\nname"] {
        assert_eq!(
            w.create_group(a, &[x], name, Color::Blue),
            Err(Error::Conflict)
        );
    }
    let group = w.create_group(a, &[x], "team", Color::Blue).unwrap();
    assert_eq!(
        w.edit_group(group, &"x".repeat(129), Color::Gray, true),
        Err(Error::Conflict)
    );
    assert_eq!(w.group(group).unwrap().name, "team");
    let mut other = Workspace::default();
    assert_eq!(other.ungroup(group), Err(Error::Unknown));
    assert_eq!(other.detach_group(group), Err(Error::Unknown));
}
