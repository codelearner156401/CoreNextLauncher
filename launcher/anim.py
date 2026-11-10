"""Global animation speed used by page transitions and button effects.

Developer options expose a 0.2x - 2.5x slider.  A larger factor means faster
animation, so durations are divided by the factor.
"""

from __future__ import annotations

MIN_SPEED = 0.2
MAX_SPEED = 2.5
DEFAULT_SPEED = 1.0

_speed = DEFAULT_SPEED


def clamp(value: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DEFAULT_SPEED
    if number != number:  # NaN
        return DEFAULT_SPEED
    return max(MIN_SPEED, min(MAX_SPEED, number))


def set_speed(value: float) -> float:
    global _speed
    _speed = clamp(value)
    return _speed


def speed() -> float:
    return _speed


def ms(base_ms: int) -> int:
    """Scale a base duration in milliseconds by the current speed."""
    return max(1, int(round(base_ms / _speed)))
