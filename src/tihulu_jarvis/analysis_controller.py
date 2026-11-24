# SPDX-License-Identifier: AGPL-3.0-only
"""Local-only, in-memory R1 controller. Not a model-facing filesystem API.

A trusted UI owns context and the live authorization callback. Workers may
compute on a selected snapshot, but only the active task may publish. The UI
must obtain results through get() again when displaying a queued event.
"""
from dataclasses import dataclass
from threading import Event, RLock
from uuid import uuid4
import hashlib

from .selected_csv import read_selected_csv
from .temperature_engine import (
    COLORS, TemperatureSeries, analyze_temperature, render_svg,
)
from .voice.m1 import Hold

MAX_ARTIFACTS = 16
ENGINE_VERSION = "temperature-series/1"
LANGUAGES = ("en", "tr", "de")


@dataclass(frozen=True)
class Context:
    session_id: str
    profile_id: str
    profile_version: int
    mode: str

    def checked(self):
        for value in (self.session_id, self.profile_id, self.mode):
            if not isinstance(value, str) or not value or len(value) > 128:
                raise Hold("invalid_analysis_context")
        if type(self.profile_version) is not int or self.profile_version < 1:
            raise Hold("invalid_analysis_context")
        return self


@dataclass(frozen=True)
class Artifact:
    id: str
    task_id: str
    parent_id: str | None
    engine: str
    series: TemperatureSeries
    color: str
    svg: bytes
    svg_sha256: str
    language: str
    summary: str


@dataclass(frozen=True)
class _Job:
    id: str
    context: Context
    generation: int
    stop: Event


def _summary(series, language):
    values = dict(n=series.count, mean=format(series.mean_c, ".6g"),
                  lo=format(series.minimum_c, ".6g"),
                  hi=format(series.maximum_c, ".6g"))
    templates = {
        "en": "{n} measurements. Mean {mean} degrees Celsius, minimum {lo}, maximum {hi}.",
        "tr": "{n} ölçüm. Ortalama {mean} santigrat derece, en düşük {lo}, en yüksek {hi}.",
        "de": "{n} Messwerte. Mittelwert {mean} Grad Celsius, Minimum {lo}, Maximum {hi}.",
    }
    return templates[language].format(**values)


class AnalysisController:
    """One active computation per controller, with bounded session artifacts.

    authorize(context, action, target) must return literal True for an active,
    scoped grant. It is called under the controller lock: it must be fast,
    nonblocking, and must not reenter the controller. It must not consult an
    LLM or expose private paths. This class is not an OS sandbox.
    """

    def __init__(self, authorize):
        if not callable(authorize):
            raise Hold("analysis_authorizer_required")
        self._authorize = authorize
        self._lock = RLock()
        self._context = None
        self._generation = 0
        self._active = None
        self._artifacts = {}

    def activate(self, context):
        """Trusted UI only. Invalidate prior jobs/results. The UI owns grant revocation."""
        if not isinstance(context, Context):
            raise Hold("invalid_analysis_context")
        context.checked()
        with self._lock:
            self._invalidate()
            self._context = context

    def _invalidate(self):
        self._generation += 1
        if self._active is not None:
            self._active.stop.set()
        self._artifacts.clear()
        self._context = None

    def hard_stop(self):
        with self._lock:
            self._invalidate()

    def cancel(self):
        """Cancel only this controller's current job, preserving prior results."""
        with self._lock:
            if self._active is not None:
                self._active.stop.set()
                return True
            return False

    @property
    def busy(self):
        with self._lock:
            return self._active is not None

    def _require_mode(self):
        if self._context is None or self._context.mode != "LOCAL_ONLY":
            raise Hold("analysis_mode_denied")

    def _allow(self, action, target):
        self._require_mode()
        try:
            allowed = self._authorize(self._context, action, target)
        except Exception:
            raise Hold("analysis_not_authorized") from None
        if allowed is not True:
            raise Hold("analysis_not_authorized")

    def _begin(self, action, target, color, language):
        if not isinstance(color, str) or color not in COLORS:
            raise Hold("chart_invalid_color")
        if not isinstance(language, str) or language not in LANGUAGES:
            raise Hold("analysis_invalid_language")
        with self._lock:
            self._allow(action, target)
            if self._active is not None:
                raise Hold("analysis_busy")
            if len(self._artifacts) >= MAX_ARTIFACTS:
                raise Hold("analysis_artifact_limit")
            job = _Job(uuid4().hex, self._context, self._generation, Event())
            self._active = job
            return job

    def _check(self, job, action, target):
        with self._lock:
            if (self._active is not job or job.stop.is_set()
                    or self._generation != job.generation
                    or self._context != job.context):
                raise Hold("analysis_cancelled")
            self._allow(action, target)
            if action == "artifact_recolor":
                self._allow("artifact_read", target)
        return False

    def _finish(self, job):
        with self._lock:
            if self._active is job:
                self._active = None

    def _publish(self, job, action, target, series, color, language, parent):
        check = lambda: self._check(job, action, target)
        svg = render_svg(series, color=color, cancelled=check)
        result = Artifact(uuid4().hex, job.id, parent, ENGINE_VERSION,
                          series, color, svg, hashlib.sha256(svg).hexdigest(),
                          language, _summary(series, language))
        with self._lock:
            self._check(job, action, target)
            # The commit and invalidation are ordered by the same lock.
            self._artifacts[result.id] = result
            return result.id

    def analyze_selected(self, path, *, color="blue", language="en", expected_identity=None):
        """Only the trusted picker supplies path. Return an opaque artifact ID."""
        if not isinstance(path, str):
            raise Hold("csv_invalid_selection")
        action = "selected_csv_analysis"
        job = self._begin(action, path, color, language)
        try:
            check = lambda: self._check(job, action, path)
            def authorized():
                check()
                return True
            data = read_selected_csv(path, authorized=authorized,
                                     cancelled=check, expected_identity=expected_identity).data
            series = analyze_temperature(data, cancelled=check)
            return self._publish(job, action, path, series, color, language, None)
        finally:
            self._finish(job)

    def get(self, artifact_id):
        """Trusted local UI only. Never pass the returned object to a model."""
        if not isinstance(artifact_id, str):
            raise Hold("analysis_artifact_not_found")
        with self._lock:
            self._allow("artifact_read", artifact_id)
            result = self._artifacts.get(artifact_id)
            if result is None:
                raise Hold("analysis_artifact_not_found")
            return result

    def recolor(self, artifact_id, *, color, language="en"):
        """Derive a new artifact from an explicitly authorized local parent."""
        if not isinstance(artifact_id, str):
            raise Hold("analysis_artifact_not_found")
        action = "artifact_recolor"
        job = self._begin(action, artifact_id, color, language)
        try:
            with self._lock:
                self._check(job, action, artifact_id)
                parent = self.get(artifact_id)
            return self._publish(job, action, artifact_id, parent.series,
                                 color, language, parent.id)
        finally:
            self._finish(job)
