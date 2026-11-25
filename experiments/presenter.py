# SPDX-License-Identifier: AGPL-3.0-only
"""Non-shipping UI handoff model; no GTK, host effects or arbitrary dispatch."""
from dataclasses import dataclass
from threading import RLock
from gateway import Refused
from workspace import Workspace

@dataclass(frozen=True)
class View:
    epoch: int
    revision: int
    state: str
    items: tuple
    can_draft: bool
    can_stop: bool

class Presenter:
    """One current workspace; consumers must refresh before rendering or acting."""
    def __init__(self):
        self._lock=RLock()
        self._workspace=None
        self._epoch=0
        self._revision=0
        self._draft=None
        self._closed=False

    def bind(self,workspace,*,confirmed=False):
        with self._lock:
            if self._closed or confirmed is not True or type(workspace) is not Workspace:
                raise Refused("explicit_workspace_required")
            if workspace is self._workspace or workspace.poll()!="ready":
                raise Refused("workspace_unavailable")
            if self._workspace is not None:
                self._workspace.close()
            self._workspace=workspace
            self._epoch+=1
            self._revision=0
            self._draft=None
            return self._epoch

    def _current(self,epoch):
        if type(epoch) is not int or self._closed or epoch!=self._epoch or self._workspace is None:
            raise Refused("stale_view")
        return self._workspace

    def view(self):
        with self._lock:
            if self._closed or self._workspace is None:
                return View(self._epoch,self._revision,"closed" if self._closed else "idle",(),False,False)
            ws=self._workspace
            state=ws.poll()
            if state in ("held","closed"):
                if self._draft is not None:
                    self._revision+=1
                self._draft=None
                return View(self._epoch,self._revision,state,(),False,False)
            try:
                items=ws.timeline()
            except Refused:
                self._draft=None
                return View(self._epoch,self._revision,ws.poll(),(),False,False)
            return View(self._epoch,self._revision,state,items,state=="ready",True)

    def _editable(self,epoch,revision):
        ws=self._current(epoch)
        if type(revision) is not int or revision!=self._revision or ws.poll()!="ready":
            raise Refused("stale_or_busy_view")
        return ws

    def draft(self,epoch,revision,text):
        with self._lock:
            ws=self._editable(epoch,revision)
            draft=ws.draft(text)
            self._revision+=1
            self._draft=draft
            return draft

    def discard(self,epoch,revision):
        with self._lock:
            self._editable(epoch,revision).discard_draft()
            self._draft=None
            self._revision+=1

    async def submit(self,epoch,revision,draft,grant,response,*,confirmed=False):
        with self._lock:
            ws=self._editable(epoch,revision)
            if confirmed is not True or draft is not self._draft or draft is None:
                raise Refused("explicit_current_draft_required")
            # Invalidate pending composer callbacks before yielding.
            self._revision+=1
            revision=self._revision
        try:
            answer=await ws.submit(draft,grant,response,confirmed=True)
            with self._lock:
                if self._current(epoch) is not ws or self._revision!=revision:
                    raise Refused("stale_view")
                ws.timeline()
                self._draft=None
                self._revision+=1
                return answer
        except BaseException:
            with self._lock:
                if self._workspace is ws and self._epoch==epoch and ws.poll() in ("held","closed"):
                    self._draft=None
                    self._revision+=1
            raise

    def stop(self,epoch):
        with self._lock:
            ws=self._current(epoch)
            self._workspace=None
            self._epoch+=1
            self._revision=0
            self._draft=None
            ws.close()

    def close(self):
        with self._lock:
            if self._workspace is not None:
                self.stop(self._epoch)
            self._closed=True
