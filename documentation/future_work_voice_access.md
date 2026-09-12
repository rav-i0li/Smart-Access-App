# Future Work — Voice Command Input Mode

**Status:** Not built. Deliberately deferred past the 10 September 2026
deadline. Captured here so it's a stated next step in the presentation,
not a scramble.

## What it is (and isn't)
A potential fourth input mode: a small, fixed-vocabulary voice command
set ("switch to hand mode", "click", "quit") for users who cannot
reliably use head, hand, or switch input, but can speak.

**This is not a conversational AI assistant.** No LLM, no chat, no
open-ended natural language. Fixed commands only, matched locally.

## Why not an LLM (e.g. a Nova-style assistant)
- SmartAccess's core differentiation and privacy claim is that nothing is
  ever sent to a server — "no company, including Google or Anthropic,
  receives your data" (per the Architecture Blueprint, Phase 11). An LLM
  voice assistant, whether cloud-hosted or a bundled model, either breaks
  that claim outright or adds a heavy dependency this project doesn't need.
- It would also reintroduce the exact "specific ecosystem" lock-in problem
  SmartAccess is positioned against — just a software ecosystem instead of
  a hardware one.
- A fixed-command approach solves the actual accessibility problem (a
  mode-switching and control method that needs no pointing or precise
  keyboard input) without any of the above trade-offs.

## Proposed approach, if built
- Offline, on-device speech recognition only — e.g. Windows Speech
  Recognition (built-in, no install) or a local library such as `vosk`.
- Small fixed grammar/command list, matched locally, no network call ever.
- Same integration pattern as the other three modes: a recognized command
  calls the same `switch_to()` / click functions already used by hotkeys
  and the (future) on-screen control panel.

## Open questions for a future build
- Social/privacy comfort of speaking commands aloud in a shared space
  (library, classroom) — flagged by outside review as a real usability
  question, not just a technical one.
- Whether Windows Speech Recognition's built-in command mode is sufficient
  on its own, before reaching for a separate library like `vosk`.
