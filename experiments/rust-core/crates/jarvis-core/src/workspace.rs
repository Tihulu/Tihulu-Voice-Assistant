// SPDX-License-Identifier: AGPL-3.0-only
//! In-memory layout fixture. No file access, persistence or authorization grants.
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

#[derive(Default)]
pub struct Workspace {
    next: u64,
    projects: BTreeMap<ProjectId, ()>,
    sessions: BTreeMap<SessionId, Option<ProjectId>>,
    windows: BTreeMap<WindowId, Vec<TabId>>,
    tabs: BTreeMap<TabId, View>,
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
        self.sessions.insert(id, project);
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
        self.sessions.get(&session).copied().ok_or(Error::Unknown)
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
        let count = self.tabs(target)?.len();
        let maximum = count - usize::from(view.window == target);
        if index > maximum {
            return Err(Error::Conflict);
        }
        let source = self.windows.get_mut(&view.window).ok_or(Error::Unknown)?;
        let old = source
            .iter()
            .position(|id| *id == tab)
            .ok_or(Error::Conflict)?;
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
        Ok(())
    }

    pub fn close_empty_window(&mut self, window: WindowId) -> Result<(), Error> {
        if !self.tabs(window)?.is_empty() {
            return Err(Error::Conflict);
        }
        self.windows.remove(&window);
        Ok(())
    }
}
