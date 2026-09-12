# Future Work — Multimodal Click Confirmation (Head + Switch)

**Status:** Not built. Deferred past the 10 September 2026 deadline. Pure
dwell-click, with tuned tolerance/cooldown, ships instead — see
documentation/click_redesign_decision.md for what's live now.

## The problem this would solve
Dwell-click has a formally studied limitation called the **Midas Touch
problem** (Jacob, 1995): in any interface where "point at it" and "select
it" are the same signal, the system can't distinguish a user resting their
attention somewhere from a user wanting to activate it. Tuning (dwell
time, tolerance radius, cooldown) reduces false positives but can't
eliminate the underlying ambiguity — deliberately holding on a target
still clicks it, by design.

## The literature-backed fix
The Midas Touch literature groups mitigations into four categories: dwell
time tuning, gaze/head gestures, smooth-pursuit tracking, and
**multimodal interaction** — pairing the continuous pointer with a
separate, deliberate confirm action. A 2017 multimodal wheelchair-control
study for upper-extremity mobility impairment explicitly recommends this
approach for exactly SmartAccess's target population.

SmartAccess already has the second modality needed: the physical switch.

## Proposed approach
A fourth interaction combination, not a fourth mode: Head Tracking drives
the cursor as it does now, but instead of (or in addition to) dwell-click,
a switch press is the click-confirm action -- "point with your head, click
with the switch." This removes the Midas Touch ambiguity entirely for any
user who has both head control and switch access, since clicking becomes
a single deliberate action again, the same as Switch Access mode already
is.

Implementation sketch:
- `arduino.poll()` (already built, `hardware/arduino_serial_manager.py`)
  checked during Head Tracking mode, not just Switch Access mode.
- A `PRESS` event while in Head mode triggers `pyautogui.click()` at the
  current cursor position, exactly like Switch Access mode already does.
- Dwell-click could remain as a fallback for users without switch access,
  selectable via a settings toggle -- not mutually exclusive with this.

## Why this is deferred, not dropped
This is a real architecture decision (does Head mode's click source
become configurable, is it additive to dwell or does it replace it,
how does the UI communicate which mode a demo is running in) worth doing
carefully rather than rushed on deadline day. The dwell tuning shipped
today (larger head tolerance, post-click cooldown) meaningfully reduces
false positives in the meantime and is itself a legitimate, literature-
backed accessibility pattern on its own.
