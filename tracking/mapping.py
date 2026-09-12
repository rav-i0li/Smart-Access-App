"""
Mapping helpers for converting a tracked point's normalized coordinate
into screen coordinates.

Fixes a common absolute-mapping issue: MediaPipe needs the whole hand
visible to detect it, so a fingertip landmark typically never reaches the
true 0.0 or 1.0 edge of frame before detection drops out — the realistic
range is often something like 0.15-0.85. expand_range() stretches that
observed range back out to the full 0-1 span before it's multiplied by
screen width/height, so reaching the edge of the camera frame reaches the
edge of the screen.
"""


def expand_range(value: float, margin: float) -> float:
    """
    Stretch `value` (assumed to realistically fall within
    [margin, 1 - margin]) back out to the full [0, 1] range, clamping
    the result. margin=0 disables the stretch (just clamps).
    """
    if margin <= 0:
        return max(0.0, min(1.0, value))
    expanded = (value - margin) / (1 - 2 * margin)
    return max(0.0, min(1.0, expanded))
