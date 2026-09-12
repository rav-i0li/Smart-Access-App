# SAGE voice lines -- scripts and generation workflow

SAGE's voice is pre-generated, not live TTS. You create each clip once
with Fish Audio, download it, and drop it in `voice/assets/`. At
runtime the app only ever plays these local files -- it never calls
Fish Audio or any other cloud service while running, so this doesn't
conflict with the "nothing sent to a server" claim on the site.

## 1. Scripts

Fish Audio's S2 model reads inline `[bracket]` tags as delivery cues
(emotion, pacing, non-speech sounds) rather than literal spoken text --
these are the same tag style used across Fish Audio's Playground and
API. Paste each script exactly as written, generate, then download and
rename the result to the filename shown.

| Filename | Script |
|---|---|
| `sage_greeting.mp3` | `[friendly] Hi, I'm SAGE. [warm] Say "Hey SAGE" any time you'd like me to switch modes or explain what I do.` |
| `sage_explain.mp3` | `[warm] Hey there! I'm SAGE — Smart Accessible Guidance Engine. [friendly] I help you control your computer hands-free, using just your head or hand movements through the webcam. [reassuring] Nothing leaves your machine — all the tracking happens locally, right here.` |
| `sage_switch_hand.mp3` | `[cheerful] Switching to hand tracking now. [pause] Go ahead and move your hand in front of the camera.` |
| `sage_switch_head.mp3` | `[cheerful] Switching to head tracking now. [pause] Just move your head naturally to control the cursor.` |
| `sage_recalibrate.mp3` | `[reassuring] Okay, recalibrating now — hold still and look at the screen.` |
| `sage_ack.mp3` | `[excited] Did someone say my name? [pause] What can I help you with?` |
| `sage_fallback.mp3` | `[apologetic] Sorry, I didn't catch that — you can say things like "switch to hand tracking" or "switch to head tracking."` |
| `sage_no_mic.mp3` | `[calm] I can't hear a microphone right now, so voice commands are off — everything else still works fine.` |

`sage_ack.mp3` no longer has to be near-instant. It originally did,
because the 4-second command-listening window used to start counting
the moment "Hey SAGE" was heard -- a long ack line would have burned
through that window before the user got a word in. As of the 13
September 2026 feedback-loop fix, the mic is suppressed while any SAGE
line plays anyway (so it doesn't hear itself talking), and the
command window now only starts once that line finishes -- so a longer,
more personality-driven ack line like the one above works fine and
doesn't cost the user any listening time. If the generated clip has a
long lead-in silence before the words start, still worth trimming that
part, since it delays how quickly SAGE visibly/audibly acknowledges you.

Feel free to swap in a different voice/tone per line, or adjust the
tags -- Fish Audio treats the tag text as free-form natural language,
not a fixed list, so descriptive tags like `[laughing nervously]` or
`[professional broadcast tone]` also work if you want to experiment.

## 1a. Pre-recorded lines (13 September 2026 addition, not Fish Audio)

These 5 clips are **fixed pre-recorded audio**, not generated from a
script via the Fish Audio workflow above -- there's no `[bracket]`-tag
script to paste for these, since they weren't produced that way. They
were supplied already named to match the **Filename** column below;
drop them into `voice/assets/` alongside the 8 files above, using
those exact filenames.

| Filename | Voice command | Triggers on |
|---|---|---|
| `sage_explain_usefulness.mp3` | "why is SmartAccess useful" | `explain_usefulness` intent |
| `sage_explain_how_it_works.mp3` | "how does SmartAccess work" | `explain_how_it_works` intent |
| `sage_explain_adaptive_sensitivity.mp3` | "what is adaptive sensitivity" | `explain_adaptive_sensitivity` intent |
| `sage_quit.mp3` | "quit SmartAccess" | `quit` intent -- app shuts down once this finishes playing, never before |
| `sage_tell_joke.mp3` | "tell me a joke" / "tell me a SmartAccess joke" | `tell_joke` intent |

SmartAccess no longer has Switch Access -- none of these 5 lines (or
any command wording for them) reference it. See `voice/sage_voice.py`
for the exact grammar phrases and keyword matching (`WAKE_GRAMMAR`,
`COMMAND_GRAMMAR`, `COMMAND_KEYWORDS`) -- not duplicated here since
this file documents the audio, not the recognition logic.

## 2. Generating clips (no API needed)

1. Go to the Fish Audio Playground (fish.audio) and sign in -- the free
   tier covers this comfortably, no paid plan needed for 8 short lines.
2. Pick a voice from the catalog (or clone one from ~15 seconds of your
   own reference audio, if you'd rather SAGE have a custom voice for
   the demo).
3. Paste one script from the table above into the text box, generate,
   listen back, and regenerate if the delivery isn't right -- tags are
   descriptive, not exact, so wording tweaks can help.
4. Download the result and rename it to match the **Filename** column
   exactly.
5. Repeat for all 8 lines, then place all 8 files directly in
   `voice/assets/` (new folder, sibling to `voice/sage_voice.py`).

## 3. One-time Vosk model download (separate from Fish Audio)

Fish Audio only produces the audio files above. The *listening* side
(recognizing "Hey SAGE" and the follow-up command) uses Vosk, a
separate, fully offline speech-recognition engine -- this is what keeps
voice commands from ever sending microphone audio anywhere.

1. Download `vosk-model-small-en-us-0.15` from
   https://alphacephei.com/vosk/models (small model, ~40MB, English).
2. Unzip it so you end up with a folder named exactly
   `vosk-model-small-en-us-0.15` sitting directly inside `voice/assets/`
   (same folder as the 8 mp3 files above).
3. `pip install vosk sounddevice pygame`

## 4. Packaging note (PyInstaller, later)

Both `voice/assets/*.mp3` and the `vosk-model-small-en-us-0.15/` folder
need to be added as PyInstaller **data files** (`--add-data`), not just
present on your dev machine -- pip installing `vosk` does not bundle
the model, and a `.py`-to-`.exe`/`.app` build won't include either set
of assets automatically. Flagging this now so it isn't a surprise at
packaging time; not something to solve tonight.

**Mac-specific:** a packaged `.app` needs an `NSMicrophoneUsageDescription`
entry added to its `Info.plist` (set via the PyInstaller `.spec` file's
Mac bundle section), or macOS silently denies microphone access with no
permission prompt at all -- the app won't crash, voice commands will
just never hear anything. Running via `python gui/app_gui.py` directly
during dev/testing doesn't need this -- macOS prompts Terminal for mic
access the normal way the first time `sounddevice` opens a stream.
