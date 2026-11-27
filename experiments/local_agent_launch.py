# SPDX-License-Identifier: AGPL-3.0-only
"""Synthetic launch boundary: explicit no-start vs uncertain process outcome.
No subprocess, PID discovery, operating-system or provider access.
"""
from dataclasses import dataclass
from threading import current_thread
from gateway import Refused
from local_agent_owned_drain import ManagedWave

@dataclass(frozen=True,eq=False)
class LaunchTicket:
    worker: str

@dataclass(frozen=True)
class LaunchObservation:
    worker: str
    state: str

class LaunchBoundary:
    """Trusted launch observer must distinguish definite absence from unknown.
    Unknown launch keeps its reservation even after cancellation. This module
    cannot establish real process absence or containment by itself.
    """
    def __init__(self,wave):
        if type(wave) is not ManagedWave:
            raise Refused("launch_wave_invalid")
        self._wave=wave
        self._owner=current_thread()
        self._entries={}
        self._members={}

    def _thread(self):
        if current_thread() is not self._owner:
            raise Refused("launch_thread")

    def _entry(self,ticket):
        self._thread()
        if type(ticket) is not LaunchTicket or type(ticket.worker) is not str:
            raise Refused("launch_ticket_unknown")
        entry=self._entries.get(ticket.worker)
        if entry is None or entry[0] is not ticket:
            raise Refused("launch_ticket_unknown")
        return entry

    def prepare(self,worker,grant,context,candidates):
        self._thread()
        handle=self._wave.start(worker,grant,context,candidates)
        ticket=LaunchTicket(worker)
        self._entries[worker]=(ticket,handle,"PREPARED")
        return ticket

    def state(self,ticket):
        return self._entry(ticket)[2]

    def observations(self):
        """Sanitized snapshot, never an absence or termination certificate."""
        self._thread()
        return tuple(LaunchObservation(worker,entry[2]) for worker,entry in sorted(self._entries.items()))

    def register_member(self,ticket,identity,context,candidates):
        _,handle,state=self._entry(ticket)
        if state!="RUNNING":
            raise Refused("launch_state_invalid")
        member=self._wave.register_member(handle,identity,context,candidates)
        self._members[member]=ticket
        return member

    def observe_member_reaped(self,ticket,member,context,candidates):
        t,handle,state=self._entry(ticket)
        if state not in ("RUNNING","ROOT_REAPED") or not any(m is member and owner is t for m,owner in self._members.items()):
            raise Refused("launch_member_unknown")
        try:
            return self._wave.observe_member_exit(member,context,candidates)
        finally:
            # A valid terminal event may release the entire family even if
            # policy validation refuses its result.
            if all(h is not handle for h in self._wave.pending()):
                del self._entries[t.worker]
                self._members={m:owner for m,owner in self._members.items() if owner is not t}
            else:
                self._members.pop(member,None)

    def before_start(self,ticket,context,candidates):
        _,handle,state=self._entry(ticket)
        if state!="PREPARED":
            raise Refused("launch_state_invalid")
        self._wave.checkpoint(handle,context,candidates)

    def started(self,ticket,context,candidates):
        t,handle,state=self._entry(ticket)
        if state!="PREPARED":
            raise Refused("launch_state_invalid")
        # The observer has reported a real start. Record ownership before
        # checking live permission so a stale start cannot lose its handle.
        self._entries[t.worker]=(t,handle,"RUNNING")
        self._wave.checkpoint(handle,context,candidates)

    def not_started(self,ticket,context,candidates):
        t,handle,state=self._entry(ticket)
        if state!="PREPARED":
            raise Refused("launch_state_invalid")
        # Trusted definite no-start observation, never a timeout inference.
        self._wave.stop()
        outcome=self._wave.observe_exit(handle,0,context,candidates)
        del self._entries[t.worker]
        return outcome

    def uncertain(self,ticket):
        t,handle,state=self._entry(ticket)
        if state not in ("PREPARED","RUNNING") or any(owner is t for owner in self._members.values()):
            raise Refused("launch_state_invalid")
        self._entries[t.worker]=(t,handle,"UNKNOWN")
        self._wave.stop()

    def stop(self):
        self._thread()
        self._wave.stop()
        return tuple(t for t,_,_ in self._entries.values())

    def observe_reaped(self,ticket,exit_code,context,candidates,*,payload=None):
        t,handle,state=self._entry(ticket)
        if state!="RUNNING":
            raise Refused("launch_reap_unproven")
        if type(exit_code) is not int or not -255<=exit_code<=255:
            raise Refused("managed_exit_invalid")
        try:
            result=self._wave.observe_exit(handle,exit_code,context,candidates,payload=payload)
            if result=="PENDING":
                self._entries[t.worker]=(t,handle,"ROOT_REAPED")
            return result
        finally:
            # An owned terminal observation may fail policy checks yet still
            # release the single root reservation. Keep the ticket on HOLD
            # when the managed family still has unobserved members.
            if all(h is not handle for h in self._wave.pending()):
                del self._entries[t.worker]
                self._members={m:owner for m,owner in self._members.items() if owner is not t}

    def inputs(self,ticket,context,candidates):
        _,handle,state=self._entry(ticket)
        if state!="RUNNING":
            raise Refused("launch_state_invalid")
        return self._wave.inputs(handle,context,candidates)
