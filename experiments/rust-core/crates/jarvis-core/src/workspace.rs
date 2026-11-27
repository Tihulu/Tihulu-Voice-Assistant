// SPDX-License-Identifier: AGPL-3.0-only
//! In-memory layout fixture. No file access, persistence or authorization grants.
use crate::chat::Chat;
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};

const MAX_OBJECTS: usize = 1024;
static NEXT_ID: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct ProjectId(u64);
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct SessionId(u64);
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct WindowId(u64);
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct TabId(u64);
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct GroupId(u64);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Color {
    Blue,
    Green,
    Purple,
    Orange,
    Gray,
}

#[derive(Debug, Eq, PartialEq)]
pub struct Group {
    pub name: String,
    pub color: Color,
    pub collapsed: bool,
    pub window: WindowId,
    pub tabs: Vec<TabId>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Error {
    Limit,
    Unknown,
    Conflict,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct View {
    session: SessionId,
    window: WindowId,
}

struct SessionRow {
    project: Option<ProjectId>,
    chat: Chat,
}

#[derive(Default)]
pub struct Workspace {
    next: u64,
    projects: BTreeMap<ProjectId, ()>,
    sessions: BTreeMap<SessionId, SessionRow>,
    windows: BTreeMap<WindowId, Vec<TabId>>,
    tabs: BTreeMap<TabId, View>,
    groups: BTreeMap<GroupId, Group>,
}

impl Workspace {
    fn issue(&mut self) -> Result<u64, Error> {
        // Bound the lifetime, not only the number of live objects.
        if self.next >= MAX_OBJECTS as u64 {
            return Err(Error::Limit);
        }
        let id = NEXT_ID
            .fetch_update(Ordering::Relaxed, Ordering::Relaxed, |n| n.checked_add(1))
            .map_err(|_| Error::Limit)?;
        self.next += 1;
        Ok(id)
    }

    pub fn project(&mut self) -> Result<ProjectId, Error> {
        let id = ProjectId(self.issue()?);
        self.projects.insert(id, ());
        Ok(id)
    }

    pub fn session(&mut self, project: Option<ProjectId>) -> Result<SessionId, Error> {
        if project.is_some_and(|id| !self.projects.contains_key(&id)) {
            return Err(Error::Unknown);
        }
        let id = SessionId(self.issue()?);
        self.sessions.insert(
            id,
            SessionRow {
                project,
                chat: Chat::new(id),
            },
        );
        Ok(id)
    }

    pub fn window(&mut self) -> Result<WindowId, Error> {
        let id = WindowId(self.issue()?);
        self.windows.insert(id, Vec::new());
        Ok(id)
    }

    pub fn tab(&mut self, session: SessionId, window: WindowId) -> Result<TabId, Error> {
        if !self.sessions.contains_key(&session) || !self.windows.contains_key(&window) {
            return Err(Error::Unknown);
        }
        let id = TabId(self.issue()?);
        self.windows
            .get_mut(&window)
            .ok_or(Error::Unknown)?
            .push(id);
        self.tabs.insert(id, View { session, window });
        Ok(id)
    }

    pub fn tab_session(&self, tab: TabId) -> Result<SessionId, Error> {
        Ok(self.tabs.get(&tab).ok_or(Error::Unknown)?.session)
    }

    pub fn session_project(&self, session: SessionId) -> Result<Option<ProjectId>, Error> {
        self.sessions
            .get(&session)
            .map(|row| row.project)
            .ok_or(Error::Unknown)
    }

    /// Trusted server-side owner accessor; not an authorization boundary.
    pub fn chat(&self, session: SessionId) -> Result<&Chat, Error> {
        self.sessions
            .get(&session)
            .map(|row| &row.chat)
            .ok_or(Error::Unknown)
    }

    /// The workspace owns the only registered chat for this session.
    pub fn chat_mut(&mut self, session: SessionId) -> Result<&mut Chat, Error> {
        self.sessions
            .get_mut(&session)
            .map(|row| &mut row.chat)
            .ok_or(Error::Unknown)
    }

    pub fn tabs(&self, window: WindowId) -> Result<&[TabId], Error> {
        self.windows
            .get(&window)
            .map(Vec::as_slice)
            .ok_or(Error::Unknown)
    }

    /// Move a view without touching its session. Index is in the resulting list.
    pub fn move_tab(&mut self, tab: TabId, target: WindowId, index: usize) -> Result<(), Error> {
        let view = *self.tabs.get(&tab).ok_or(Error::Unknown)?;
        if self.group_of(tab).is_some() {
            return Err(Error::Conflict);
        }
        let count = self.tabs(target)?.len();
        let maximum = count - usize::from(view.window == target);
        if index > maximum {
            return Err(Error::Conflict);
        }
        let source = self.windows.get(&view.window).ok_or(Error::Unknown)?;
        let old = source
            .iter()
            .position(|id| *id == tab)
            .ok_or(Error::Conflict)?;
        // Validate the resulting target before mutating either window.
        let mut destination = self.tabs(target)?.to_vec();
        if view.window == target {
            destination.remove(old);
        }
        if self.splits_group(&destination, index) {
            return Err(Error::Conflict);
        }
        let source = self.windows.get_mut(&view.window).ok_or(Error::Unknown)?;
        source.remove(old);
        self.windows
            .get_mut(&target)
            .ok_or(Error::Unknown)?
            .insert(index, tab);
        self.tabs.get_mut(&tab).ok_or(Error::Unknown)?.window = target;
        Ok(())
    }

    pub fn detach(&mut self, tab: TabId) -> Result<WindowId, Error> {
        if !self.tabs.contains_key(&tab) {
            return Err(Error::Unknown);
        }
        if self.group_of(tab).is_some() {
            return Err(Error::Conflict);
        }
        let target = self.window()?;
        self.move_tab(tab, target, 0)?;
        Ok(target)
    }

    pub fn close_tab(&mut self, tab: TabId) -> Result<(), Error> {
        let view = *self.tabs.get(&tab).ok_or(Error::Unknown)?;
        let list = self.windows.get_mut(&view.window).ok_or(Error::Unknown)?;
        let index = list
            .iter()
            .position(|id| *id == tab)
            .ok_or(Error::Conflict)?;
        list.remove(index);
        self.tabs.remove(&tab);
        if let Some(group) = self.group_of(tab) {
            let row = self.groups.get_mut(&group).ok_or(Error::Unknown)?;
            row.tabs.retain(|id| *id != tab);
            if row.tabs.is_empty() {
                self.groups.remove(&group);
            }
        }
        Ok(())
    }

    pub fn close_empty_window(&mut self, window: WindowId) -> Result<(), Error> {
        if !self.tabs(window)?.is_empty() {
            return Err(Error::Conflict);
        }
        self.windows.remove(&window);
        Ok(())
    }

    fn group_of(&self, tab: TabId) -> Option<GroupId> {
        self.groups
            .iter()
            .find_map(|(id, group)| group.tabs.contains(&tab).then_some(*id))
    }

    fn splits_group(&self, list: &[TabId], index: usize) -> bool {
        if index == 0 || index >= list.len() {
            return false;
        }
        self.group_of(list[index - 1])
            .is_some_and(|id| self.group_of(list[index]) == Some(id))
    }

    fn valid_name(name: &str) -> bool {
        !name.trim().is_empty() && name.len() <= 128 && !name.chars().any(char::is_control)
    }

    pub fn create_group(
        &mut self,
        window: WindowId,
        tabs: &[TabId],
        name: &str,
        color: Color,
    ) -> Result<GroupId, Error> {
        if !Self::valid_name(name) || tabs.is_empty() {
            return Err(Error::Conflict);
        }
        let list = self.tabs(window)?;
        if tabs.len() > list.len()
            || !list.windows(tabs.len()).any(|slice| slice == tabs)
            || tabs.iter().any(|tab| self.group_of(*tab).is_some())
        {
            return Err(Error::Conflict);
        }
        let id = GroupId(self.issue()?);
        self.groups.insert(
            id,
            Group {
                name: name.to_owned(),
                color,
                collapsed: false,
                window,
                tabs: tabs.to_vec(),
            },
        );
        Ok(id)
    }

    pub fn group(&self, id: GroupId) -> Result<&Group, Error> {
        self.groups.get(&id).ok_or(Error::Unknown)
    }

    pub fn edit_group(
        &mut self,
        id: GroupId,
        name: &str,
        color: Color,
        collapsed: bool,
    ) -> Result<(), Error> {
        if !Self::valid_name(name) {
            return Err(Error::Conflict);
        }
        let group = self.groups.get_mut(&id).ok_or(Error::Unknown)?;
        group.name = name.to_owned();
        group.color = color;
        group.collapsed = collapsed;
        Ok(())
    }

    pub fn ungroup(&mut self, id: GroupId) -> Result<(), Error> {
        self.groups.remove(&id).map(|_| ()).ok_or(Error::Unknown)
    }

    /// Index is relative to the target after removing the moving group.
    pub fn move_group(&mut self, id: GroupId, target: WindowId, index: usize) -> Result<(), Error> {
        let group = self.group(id)?;
        let source = group.window;
        let members = group.tabs.clone();
        let source_list = self.tabs(source)?;
        let start = source_list
            .windows(members.len())
            .position(|slice| slice == members.as_slice())
            .ok_or(Error::Conflict)?;
        let mut remaining = source_list.to_vec();
        remaining.drain(start..start + members.len());
        let mut destination = if source == target {
            remaining.clone()
        } else {
            self.tabs(target)?.to_vec()
        };
        if index > destination.len() || self.splits_group(&destination, index) {
            return Err(Error::Conflict);
        }
        destination.splice(index..index, members.iter().copied());
        if source != target {
            self.windows.insert(source, remaining);
        }
        self.windows.insert(target, destination);
        for tab in members {
            self.tabs.get_mut(&tab).ok_or(Error::Unknown)?.window = target;
        }
        self.groups.get_mut(&id).ok_or(Error::Unknown)?.window = target;
        Ok(())
    }

    pub fn detach_group(&mut self, id: GroupId) -> Result<WindowId, Error> {
        self.group(id)?;
        let target = self.window()?;
        self.move_group(id, target, 0)?;
        Ok(target)
    }
}
