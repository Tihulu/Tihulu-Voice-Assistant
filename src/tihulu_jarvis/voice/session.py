# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""One local session, with epoch checks across every asynchronous turn."""
import asyncio
import re

from .m1 import Hold, LANGUAGES, LanguageContext, Message, complete_turn, lexical_identifier, route_language, valid_response


class Session:
    def __init__(self):
        self.epoch, self.history, self.context = 0, (), LanguageContext()
        self.active = None
        self._invalidated = False

    def invalidate(self, clear=False):
        if not self._invalidated:
            self._invalidated = True
            self.epoch += 1
            if self.active is not None and not self.active.done():
                self.active.cancel()
        if clear:
            self.history, self.context = (), LanguageContext()

    def own(self, run, *, loop=None):
        if self.active is not None and not self.active.done():
            raise Hold("operation_busy")
        self.epoch += 1
        self._invalidated = False
        self.active = (loop or asyncio.get_running_loop()).create_task(run(self.epoch))
        return self.active

    def check(self, epoch):
        if epoch != self.epoch:
            raise Hold("turn_cancelled")

    async def reply(self, user, jan, epoch, voices=None):
        self.check(epoch)
        history = self.history
        if voices is not None:
            done = await complete_turn(user,self.context,history,jan,voices,cancelled=lambda: epoch!=self.epoch)
            self.check(epoch)
            self.history, self.context = done.history, done.context
            return done.display_text, done.speech
        decision = route_language(user,self.context)
        message = Message("user", user)
        for attempt in range(2):
            self.check(epoch)
            system = "Answer naturally in " + LANGUAGES[decision.response_language] + ". Preserve conversation facts. Return only your final answer."
            if attempt:
                system += " Correct the response language and omit internal analysis."
            result = await jan((Message("system",system),*history,message))
            self.check(epoch)
            if (isinstance(result,str) and result.strip() and len(result)<=32000
                    and not re.search(r"</?(?:think|analysis)\b",result,re.I)
                    and valid_response(result,decision.response_language,lexical_identifier)):
                self.history, self.context = (*history,message,Message("assistant",result)),decision.next_context
                return result, None
        raise Hold("response_rejected_after_one_retry")
