"""
tracking/one_euro_filter.py

NEW FILE — does not replace smoothing.py, which is untouched and still
used conceptually as a reference. This replaces MovingAverageSmoother
specifically for Hand Tracking's cursor position, because a fixed-window
moving average has an inherent trade-off it can't escape: wide enough to
kill jitter when still, and it necessarily lags and goes "choppy" on fast
movement, since it's always averaging in stale positions from several
frames ago.

Reference: Casiez, Roussel, Vogel, "1-Euro Filter: A Simple Speed-based
Low-pass Filter for Noisy Input in Interactive Systems", CHI 2012. This is
a standard, widely-reused algorithm (used by e.g. Monado's hand tracking,
several MediaPipe-based cursor projects) specifically because it adapts:
heavy smoothing when the signal is nearly still, light smoothing (fast
response, low lag) when it's moving quickly. Two tunable parameters:
    - min_cutoff: baseline smoothing strength when nearly still. Lower =
      smoother but more lag at low speed.
    - beta: how much smoothing loosens as speed increases. Higher = less
      lag during fast movement, at the cost of a bit more residual jitter
      at high speed.
"""

import math
import time


def _smoothing_factor(t_e, cutoff):
    r = 2 * math.pi * cutoff * t_e
    return r / (r + 1)


class _LowPassFilter:
    def __init__(self):
        self._y = None

    def filter(self, value, alpha):
        if self._y is None:
            self._y = value
        else:
            self._y = alpha * value + (1.0 - alpha) * self._y
        return self._y

    def reset(self):
        self._y = None


class OneEuroFilter:
    """1D One Euro Filter. Feed it a noisy scalar signal over time, get a
    smoothed value back that stays stable when the signal is still and
    responsive when it's moving fast."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.5, d_cutoff: float = 1.0,
                 max_dt_s: float = 0.1):
        self.min_cutoff = min_cutoff
        self.beta = beta
        self.d_cutoff = d_cutoff
        # Caps the time delta used for the speed estimate. Without this, a
        # single irregular frame gap (e.g. a rendering hiccup from the
        # preview window) produces an artificially huge "speed" for that
        # one frame, which briefly overrides the smoothing entirely and
        # shows up as a stutter -- this is what made the filter MORE
        # sensitive to timing irregularity than the plain moving average
        # it replaced, not less.
        self.max_dt_s = max_dt_s

        self._x_filter = _LowPassFilter()
        self._dx_filter = _LowPassFilter()
        self._t_prev = None

    def filter(self, x: float, t: float = None) -> float:
        t = t if t is not None else time.time()

        if self._t_prev is None:
            self._t_prev = t
            self._x_filter.filter(x, 1.0)
            self._dx_filter.filter(0.0, 1.0)
            return x

        t_e = t - self._t_prev
        if t_e <= 0:
            return self._x_filter._y
        self._t_prev = t
        t_e = min(t_e, self.max_dt_s)

        # Estimate current speed of the signal.
        a_d = _smoothing_factor(t_e, self.d_cutoff)
        dx = (x - self._x_filter._y) / t_e
        dx_hat = self._dx_filter.filter(dx, a_d)

        # Faster movement -> higher cutoff -> less smoothing -> less lag.
        cutoff = self.min_cutoff + self.beta * abs(dx_hat)
        a = _smoothing_factor(t_e, cutoff)
        return self._x_filter.filter(x, a)

    def reset(self):
        self._t_prev = None
        self._x_filter.reset()
        self._dx_filter.reset()


class OneEuroFilter2D:
    """Applies an independent OneEuroFilter to x and y, for smoothing a
    stream of 2D points such as a tracked fingertip or cursor position."""

    def __init__(self, min_cutoff: float = 1.0, beta: float = 0.5, d_cutoff: float = 1.0,
                 max_dt_s: float = 0.1):
        self._fx = OneEuroFilter(min_cutoff, beta, d_cutoff, max_dt_s)
        self._fy = OneEuroFilter(min_cutoff, beta, d_cutoff, max_dt_s)

    def filter(self, x: float, y: float, t: float = None):
        t = t if t is not None else time.time()
        return self._fx.filter(x, t), self._fy.filter(y, t)

    def reset(self):
        self._fx.reset()
        self._fy.reset()
