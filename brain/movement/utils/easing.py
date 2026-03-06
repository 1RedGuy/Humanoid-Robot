"""Easing functions for servo animations.

All functions take a normalised time ``t ∈ [0, 1]`` and return a normalised
position ``p ∈ [0, 1]``.  Use :func:`interpolate` to map between two angle
values with a chosen easing.
"""

from __future__ import annotations

import math


# ── elementary easings ───────────────────────────────────────────────────────

def linear(t: float) -> float:
    """No easing — constant-speed movement."""
    return t


def ease_in(t: float) -> float:
    """Quadratic ease-in — starts slow, then accelerates."""
    return t * t


def ease_out(t: float) -> float:
    """Quadratic ease-out — starts fast, then decelerates."""
    return t * (2.0 - t)


def ease_in_out(t: float) -> float:
    """Quadratic ease-in-out — slow start and end, faster in the middle.

    Good default for smooth, organic neck / head drift movements.
    """
    if t < 0.5:
        return 2.0 * t * t
    return -1.0 + (4.0 - 2.0 * t) * t


# ── cubic easings ─────────────────────────────────────────────────────────────

def ease_in_cubic(t: float) -> float:
    """Cubic ease-in — strong slow start, sharp acceleration."""
    return t * t * t


def ease_out_cubic(t: float) -> float:
    """Cubic ease-out — sharp start, strong deceleration."""
    u = t - 1.0
    return u * u * u + 1.0


def ease_in_out_cubic(t: float) -> float:
    """Cubic ease-in-out — pronounced S-curve, very smooth endpoints."""
    if t < 0.5:
        return 4.0 * t * t * t
    u = t - 1.0
    return 1.0 + 4.0 * u * u * u


# ── saccade / snap ────────────────────────────────────────────────────────────

def snap(t: float) -> float:
    """Saccade-style snap — moves almost instantly then holds.

    Human eye saccades complete in ~20-200 ms.  This exponential curve
    reaches ~95 % of the target in the first quarter of the duration,
    making servo moves feel like genuine fast eye movements.
    """
    return 1.0 - math.exp(-6.0 * t)


# ── convenience wrapper ───────────────────────────────────────────────────────

def interpolate(
    start: float,
    end: float,
    t: float,
    fn=None,
) -> float:
    """Return the interpolated angle between *start* and *end* at time *t*.

    Parameters
    ----------
    start:
        Starting angle in degrees.
    end:
        Target angle in degrees.
    t:
        Normalised time, automatically clamped to ``[0, 1]``.
    fn:
        Easing function to apply.  Defaults to :func:`ease_in_out`.

    Returns
    -------
    float
        Interpolated angle in degrees.
    """
    if fn is None:
        fn = ease_in_out
    t_clamped = max(0.0, min(1.0, t))
    return start + (end - start) * fn(t_clamped)
