# SPDX-License-Identifier: AGPL-3.0-only
# Distributed without warranty; see LICENSE for the full terms.
"""One local session, with epoch checks across every asynchronous turn."""
import asyncio
import re
import json

from .. import attachments
from .jan import LIMIT

from .m1 import Hold, LANGUAGES, LanguageContext, Message, complete_turn, lexical_identifier, route_language, valid_response


class Session:
    def __init__(self):
        self.epoch, self.history, self.context = 0, (), LanguageContext()
        self.active = None
        self._history_route = None
        self._invalidated = False

    def invalidate(self, clear=False):
        if not self._invalidated:
            self._invalidated = True
            self.epoch += 1
            if self.active is not None and not self.active.done():
                self.active.cancel()
        if clear:
            self.history, self.context = (), LanguageContext()
            self._history_route = None

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

    def require_route(self, route):
        """A prior conversation may not silently cross a changed trust route.

        The trusted caller supplies an immutable route identity; it is not
        taken from model output. Unbound standalone sessions retain their
        existing contract. Desktop turns must supply a route on every reply.
        """
        if self.history and route != self._history_route:
            raise Hold("conversation_route_changed_end_conversation")

    def prepare_text(self, user, *, attachment=None, model=None):
        """Validate the trusted composer independently from untrusted file data.

        Bound the complete UTF-8 wire request including history and both retry
        instructions before the UI consumes a draft. A standalone caller with
        no model ID reserves the worst-case space for a valid 256-char ID.
        """
        decision = route_language(user, self.context)
        content = user
        if attachment is not None:
            if not isinstance(attachment, attachments.Attachment):
                raise Hold("attachment_not_text")
            content += "\n\n" + attachments.prompt_block(attachment.checked())
        message = Message("user", content)
        for attempt in range(2):
            system = self._text_instruction(decision, attempt)
            request = (Message("system", system), *self.history, message)
            # Same serialization as the Jan transport; false is one byte
            # longer than true, so this covers both streaming and full replies.
            payload = {"model": model if isinstance(model, str) else "\U0001f600" * 256,
                       "stream": False,
                       "messages": [{"role": m.role, "content": m.content} for m in request]}
            try:
                size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            except (UnicodeError, ValueError, TypeError):
                raise Hold("invalid_input") from None
            if size > LIMIT:
                raise Hold("jan_request_too_large")
        return decision, message

    @staticmethod
    def _text_instruction(decision, attempt):
        system = "Answer naturally in " + LANGUAGES[decision.response_language] + ". Preserve conversation facts. Return only your final answer."
        if attempt:
            system += " Correct the response language and omit internal analysis."
        return system

    async def reply(self, user, jan, epoch, voices=None, on_delta=None, *, route=None,
                    attachment=None, model=None, authorize=lambda: True):
        self.check(epoch)
        self.require_route(route)
        history = self.history
        if voices is not None:
            if attachment is not None:
                raise Hold("attachment_voice_unsupported")
            done = await complete_turn(user,self.context,history,jan,voices,cancelled=lambda: epoch!=self.epoch)
            self.check(epoch)
            self.history, self.context = done.history, done.context
            self._history_route = route
            return done.display_text, done.speech
        decision, message = self.prepare_text(user, attachment=attachment, model=model)
        # A client that can stream is asked to stream: the same reply, shown as
        # the server sends it. A client without that capability still answers.
        streaming = on_delta is not None and callable(getattr(jan, "chat", None))
        for attempt in range(2):
            self.check(epoch)
            if authorize() is not True:
                raise Hold("attachment_context_changed")
            system = self._text_instruction(decision, attempt)
            result = await (jan.chat((Message("system",system),*history,message), on_delta=on_delta)
                            if streaming else jan((Message("system",system),*history,message)))
            self.check(epoch)
            if (isinstance(result,str) and result.strip() and len(result)<=32000
                    and not re.search(r"</?(?:think|analysis)\b",result,re.I)
                    and valid_response(result,decision.response_language,lexical_identifier)):
                self.history, self.context = (*history,message,Message("assistant",result)),decision.next_context
                self._history_route = route
                return result, None
        raise Hold("response_rejected_after_one_retry")
