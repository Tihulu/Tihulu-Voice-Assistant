# SPDX-License-Identifier: AGPL-3.0-only
"""Deterministic R1 temperature-series engine, with no host or network I/O.

The caller must already hold an authorized, bounded snapshot of the selected
file. This module does not grant permissions, open paths or interpret prompts.
"""
import csv
import hashlib
import io
import math
import re
from dataclasses import dataclass

from .voice.m1 import Hold

MAX_BYTES = 262144
MAX_ROWS = 10000
MAX_MAGNITUDE = 1e12
NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
COLORS = {"blue": "#2563eb", "green": "#15803d", "orange": "#c2410c"}


@dataclass(frozen=True)
class TemperatureSeries:
    input_sha256: str
    points: tuple[tuple[float, float], ...]
    mean_c: float
    minimum_c: float
    maximum_c: float
    delta_c: float
    duration_s: float

    @property
    def count(self):
        return len(self.points)


def _checkpoint(cancelled):
    if cancelled():
        raise Hold("analysis_cancelled")


def _number(value):
    value = value.strip()
    if len(value) > 64 or not NUMBER.fullmatch(value):
        raise Hold("csv_invalid_number")
    number = float(value)
    if not math.isfinite(number) or abs(number) > MAX_MAGNITUDE:
        raise Hold("csv_invalid_number")
    return number


def analyze_temperature(data: bytes, *, cancelled=lambda: False):
    """Strict UTF-8 CSV with time_s,temperature_c columns and increasing time.

    Reject invalid input rather than silently dropping or repairing rows.
    A cancellation callback is supplied by the trusted task owner.
    """
    _checkpoint(cancelled)
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise Hold("csv_invalid_size")
    if b"\x00" in data:
        raise Hold("csv_invalid_encoding")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Hold("csv_invalid_encoding") from None
    rows = csv.reader(io.StringIO(text, newline=""), strict=True)
    points = []
    try:
        if next(rows, None) != ["time_s", "temperature_c"]:
            raise Hold("csv_invalid_header")
        for row in rows:
            _checkpoint(cancelled)
            if len(points) >= MAX_ROWS:
                raise Hold("csv_too_many_rows")
            if len(row) != 2:
                raise Hold("csv_invalid_row")
            t, c = map(_number, row)
            if points and t <= points[-1][0]:
                raise Hold("csv_time_not_increasing")
            points.append((t, c))
    except csv.Error:
        raise Hold("csv_malformed") from None
    if not points:
        raise Hold("csv_empty")
    values = [c for _, c in points]
    result = TemperatureSeries(
        hashlib.sha256(data).hexdigest(), tuple(points),
        math.fsum(values) / len(values), min(values), max(values),
        values[-1] - values[0], points[-1][0] - points[0][0],
    )
    _checkpoint(cancelled)
    return result


def render_svg(series: TemperatureSeries, *, color="blue",
               cancelled=lambda: False):
    """Produce a standalone static chart from trusted engine output.

    No embedded scripts, external references, user-controlled markup or
    filesystem output. Recoloring does not change the underlying data.
    """
    _checkpoint(cancelled)
    if not isinstance(color, str) or color not in COLORS:
        raise Hold("chart_invalid_color")
    if not isinstance(series, TemperatureSeries) or not series.points:
        raise Hold("chart_invalid_series")
    x0, x1 = series.points[0][0], series.points[-1][0]
    y0, y1 = series.minimum_c, series.maximum_c
    # Equal ranges are drawn in the middle; the axes remain labelled.
    def project(t, c):
        x = 64 + 540 * ((t - x0) / (x1 - x0) if x1 != x0 else .5)
        y = 254 - 210 * ((c - y0) / (y1 - y0) if y1 != y0 else .5)
        return f"{x:.3f},{y:.3f}"

    coords = []
    for t, c in series.points:
        _checkpoint(cancelled)
        coords.append(project(t, c))
    stroke = COLORS[color]
    line = " ".join(coords)
    marker = ""
    if len(coords) == 1:
        x, y = coords[0].split(",")
        marker = f'<circle cx="{x}" cy="{y}" r="4" fill="{stroke}"/>'
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" width="640" height="320" '
        'viewBox="0 0 640 320" role="img" aria-label="Temperature versus time">'
        '<rect width="640" height="320" fill="white"/>'
        '<g fill="#111827" font-family="sans-serif" font-size="12">'
        '<text x="320" y="22" text-anchor="middle">Temperature versus time</text>'
        '<text x="334" y="302" text-anchor="middle">Time (s)</text>'
        '<text x="14" y="150" transform="rotate(-90 14 150)" '
        'text-anchor="middle">Temperature (°C)</text>'
        f'<text x="64" y="272">{x0:.6g}</text>'
        f'<text x="604" y="272" text-anchor="end">{x1:.6g}</text>'
        f'<text x="58" y="254" text-anchor="end">{y0:.6g}</text>'
        f'<text x="58" y="44" text-anchor="end">{y1:.6g}</text>'
        '</g><path d="M64 44 V254 H604" stroke="#374151" fill="none"/>'
        f'<polyline points="{line}" stroke="{stroke}" stroke-width="2" fill="none"/>'
        f'{marker}</svg>'
    )
    _checkpoint(cancelled)
    return svg.encode("utf-8")
