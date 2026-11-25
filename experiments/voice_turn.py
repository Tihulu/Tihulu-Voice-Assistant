# SPDX-License-Identifier: AGPL-3.0-only
"""Staged local voice-turn replay. Synthetic inputs only, no devices or transport.

A reviewed transcript never submits itself. Each stage requires fresh exact
local consent, and TTS returns supplied fixture WAV bytes, never plays audio.
"""
from threading import RLock
from gateway import Context, FixtureGateway, Request, Refused, token
from inventory import Inventory
from voice_pipeline import revalidate
from voice_review import review, target as stt_target, wav_bytes
from jan_replay import Turn, request_bytes, replay
from adapter_protocol import prepare, text_bytes


def reason_target(candidate, identity, url, text):
    return prepare(candidate, identity, "llm",
                   request_bytes(candidate,url,(Turn("user",text),))).target


def speech_target(candidate, identity, text):
    return prepare(candidate,identity,"tts",text_bytes(text)).target


class VoiceTurn:
    def __init__(self, gateway, context, inventory, expected, candidates, identity):
        if (type(gateway) is not FixtureGateway or type(context) is not Context
                or gateway.context != context or type(inventory) is not Inventory
                or inventory.capacity_snapshot()[0] is None):
            raise Refused("metered_owned_context_required")
        self._id=token(identity)
        self._g,self._ctx,self._inventory=gateway,context,inventory
        self._plan=revalidate(expected,context,candidates)
        self._stt,self._llm,self._tts=self._plan.models
        if self._llm.provider!="jan":
            raise Refused("jan_replay_required")
        self._lock=RLock()
        self._checks=[]
        self._draft=self._answer=None
        self._state="ready"
        self._lease=inventory.lease_pipeline(expected,context,candidates)

    @property
    def state(self):
        with self._lock:
            return self._state

    def _release(self):
        if self._lease is not None:
            self._inventory.release_pipeline(self._lease)
            self._lease=None

    def _discard(self,state):
        self._state=state
        self._draft=self._answer=None
        try:
            for grant,_ in self._checks:
                self._g.revoke(grant.identity)
        finally:
            self._checks.clear()
            self._release()

    def _fail(self):
        if self._state!="closed":
            self._discard("held")

    def _live(self,state):
        if self._state!=state:
            raise Refused("voice_stage_unavailable")
        try:
            for grant,req in self._checks:
                self._g.checkpoint(grant,req)
            if self._state!=state:
                raise Refused("voice_stage_unavailable")
        except Refused:
            self._fail()
            raise

    def _bind(self,grant,target):
        req=Request(self._ctx,"engine",target,"local")
        self._g.checkpoint(grant,req)
        self._checks.append((grant,req))
        return req

    def transcribe(self,pcm,grant,events):
        with self._lock:
            self._live("ready")
            try:
                self._bind(grant,stt_target(self._stt,self._id,pcm))
                self._state="transcribing"
                draft=review(self._g,self._ctx,self._stt,self._id,pcm,grant,events)
                self._live("transcribing")
                self._draft=draft
                self._state="review"
                return draft
            except BaseException:
                self._fail()
                raise

    async def reason(self,draft,edited_text,grant,url,response,*,confirmed=False):
        with self._lock:
            self._live("review")
            if confirmed is not True or draft is not self._draft:
                raise Refused("explicit_current_draft_required")
            if not text_bytes(edited_text).strip():
                raise Refused("empty_review")
            try:
                self._bind(grant,reason_target(self._llm,self._id,url,edited_text))
                self._state="reasoning"
            except BaseException:
                self._fail()
                raise
        try:
            data=await replay(self._g,grant,self._ctx,self._llm,self._id,url,
                              (Turn("user",edited_text),),response)
            with self._lock:
                self._live("reasoning")
                self._answer=data.decode("utf-8")
                self._draft=None
                self._state="answer"
                return self._answer
        except BaseException:
            with self._lock:
                self._fail()
            raise

    def speech(self,grant,fixture_pcm,*,confirmed=False):
        with self._lock:
            self._live("answer")
            if confirmed is not True:
                raise Refused("explicit_speech_confirmation_required")
            try:
                req=self._bind(grant,speech_target(self._tts,self._id,self._answer))
                self._state="synthesizing"
                self._g.claim(grant,req)
                # Supplied test samples are not synthesized speech.
                data=wav_bytes(fixture_pcm)
                self._live("synthesizing")
                self._release()
                self._live("synthesizing")
                self._draft=self._answer=None
                self._state="completed"
                return data
            except BaseException:
                self._fail()
                raise

    def poll(self):
        """Explicit owner tick; not background cleanup."""
        with self._lock:
            if self._state not in ("closed","held","completed"):
                try:
                    self._live(self._state)
                except Refused:
                    pass
            return self._state

    def close(self):
        with self._lock:
            self._discard("closed")
