"""
voice/sage_voice.py

NEW FILE. Goes in: <project root>/voice/sage_voice.py (new "voice"
folder, sibling to backend/, gui/, gestures/, etc.)

SAGE ("Smart Accessible Guidance Engine") voice command layer -- a
fixed keyword-to-action system, NOT a full AI assistant, per the
original scope decision. It does two independent things:

1. PLAYBACK. Plays pre-generated voice line clips through
   pygame.mixer. Clips are generated ONCE, offline, using the Fish
   Audio Playground during development -- see
   documentation/voice_lines.md for the exact scripts and the
   generate/download workflow. At RUNTIME this class never talks to
   Fish Audio or any other cloud service; it only plays local files
   already sitting in voice/assets/.

2. WAKE-WORD LISTENING. Runs a background thread listening for
   "hey sage" via Vosk -- a fully offline, on-device speech
   recognition engine. No microphone audio ever leaves the machine at
   runtime. This directly resolves the earlier open conflict between
   a voice feature and the project's "nothing sent to a server" claim:
   Fish Audio is a content-creation-time tool only, Vosk is the
   runtime listener, and the two never touch the same code path.

   Once "hey sage" is heard, a short follow-up window listens for one
   of a small fixed set of commands (switch to hand/head tracking,
   explain yourself, recalibrate) via loose keyword matching -- Vosk's
   small model isn't perfectly accurate, and this is a fixed command
   set rather than open dialogue, so a couple of keywords per intent
   is more robust than requiring an exact phrase match.

Requires: vosk, sounddevice, pygame
    pip install vosk sounddevice pygame
Also requires the Vosk English model downloaded once (NOT pip-installed
-- see documentation/voice_lines.md step 4) and unzipped into
voice/assets/vosk-model-small-en-us-0.15/. That ~40MB model directory
also needs to be included in the PyInstaller build (added as a data
file), not just present at dev time, or the packaged .exe/.app will
fail to find it.

13 September 2026 addition -- DEBUG INSTRUMENTATION. Same approach used
to chase the settings-slider bug: rather than guess why the console is
silent, print at every stage of the pipeline (mic opened -> thread
running -> audio actually arriving -> Vosk recognizing ANYTHING, partial
or final -> wake phrase matched -> command dispatched). A silent
console after this change is itself informative -- whichever print
line is the LAST one you see tells you exactly which stage is failing.
These are plain prints, not gated behind a debug flag, since this is a
temporary diagnostic pass -- strip them (or swap for a logger) once
voice commands are confirmed working end to end.

13 September 2026 addition -- 5 new fixed commands: why SmartAccess is
useful, how it works, what adaptive sensitivity is, quit, and a joke.
Same architecture as every existing command: a grammar-restricted
phrase in WAKE_GRAMMAR/COMMAND_GRAMMAR, a keyword entry in
COMMAND_KEYWORDS, an intent branch in _dispatch(), and a pre-recorded
(not TTS-generated) mp3 registered in VOICE_LINES. Quit is the one
exception with extra behavior: it needs to shut the whole app down
once its line finishes playing, which this class still can't do
directly (see "2. WAKE-WORD LISTENING" above -- it never imports the
GUI). See the new on_quit callback on __init__ for how that handoff
works without breaking that boundary. SmartAccess no longer has Switch
Access, so none of the new commands or explain lines reference it.
"""

import json
import queue
import threading
import time
from pathlib import Path

import pygame
import sounddevice as sd
import vosk

from backend.tracking_engine import MODE_HEAD, MODE_HAND

VOICE_ASSETS_DIR = Path(__file__).parent / "assets"
VOSK_MODEL_PATH = VOICE_ASSETS_DIR / "vosk-model-small-en-us-0.15"

# name -> filename. Generate these via Fish Audio (documentation/voice_lines.md)
# and drop them in voice/assets/ using exactly these filenames.
VOICE_LINES = {
    "greeting": "sage_greeting.mp3",
    "explain": "sage_explain.mp3",
    "switch_hand": "sage_switch_hand.mp3",
    "switch_head": "sage_switch_head.mp3",
    "recalibrate": "sage_recalibrate.mp3",
    "ack": "sage_ack.mp3",
    "fallback": "sage_fallback.mp3",
    "no_mic": "sage_no_mic.mp3",
    # 13 September 2026 addition -- 5 new fixed pre-recorded lines
    # (not Fish Audio TTS -- these mp3s were supplied already-named
    # and are just registered here like the lines above).
    "explain_usefulness": "sage_explain_usefulness.mp3",
    "explain_how_it_works": "sage_explain_how_it_works.mp3",
    "explain_adaptive_sensitivity": "sage_explain_adaptive_sensitivity.mp3",
    "quit": "sage_quit.mp3",
    "tell_joke": "sage_tell_joke.mp3",
}

WAKE_PHRASE = "hey sage"
COMMAND_WINDOW_S = 4.0  # how long after the wake phrase SAGE keeps listening for a command

# 13 September 2026 addition -- feedback-loop fix. Confirmed via live
# console log: with playback and listening both active, the microphone
# picks up SAGE's OWN voice lines coming out of the speakers and Vosk
# transcribes them as if the user had said them (e.g. after dispatching
# switch_head, the very next "recognized" text was SAGE's own
# confirmation line, word for word). ECHO_TAIL_S is a short grace period
# added AFTER a clip finishes playing, on top of the playback duration
# itself, to also swallow the tail end of room echo / mic ringing rather
# than cutting suppression off at the exact instant playback ends.
ECHO_TAIL_S = 0.6

# Deliberately loose: substring keyword matching per intent, not exact
# phrases -- see module docstring for why.
COMMAND_KEYWORDS = {
    "switch_hand": ["hand"],
    "switch_head": ["head"],
    "recalibrate": ["recalibrate", "calibrate"],
    "explain": ["explain", "what are you", "how do you", "who are you"],
    # 13 September 2026 addition -- 5 new intents. Checked against the
    # generic "explain" entry above for keyword overlap: none of these
    # substrings ("useful", "how does", "adaptive sensitivity", "quit",
    # "joke") appear in any existing intent's keyword list or vice
    # versa, so dict order doesn't matter here -- first-match-wins
    # can't misfire between old and new intents.
    "explain_usefulness": ["useful"],
    "explain_how_it_works": ["how does", "how it works"],
    "explain_adaptive_sensitivity": ["adaptive sensitivity"],
    "quit": ["quit"],
    "tell_joke": ["joke"],
}

# 13 September 2026 addition -- GRAMMAR CONSTRAINTS. Confirmed via live
# testing that Vosk's small model, left to search its entire English
# vocabulary, regularly mishears "sage" as "sewage", "siege", "a seat",
# etc. -- an unconstrained decoder has to consider every word it knows
# for every sound, and "sage" is an uncommon word for it to land on by
# chance. Vosk supports passing a JSON list of allowed phrases to the
# recognizer (its "grammar" feature); this restricts the decoder to only
# choosing among those phrases, which is dramatically more accurate for
# a fixed command-set app like this one, where we never need open
# vocabulary recognition in the first place. "[unk]" is included as a
# catch-all so speech that matches nothing in the list is reported as
# unknown rather than force-mapped onto the closest allowed phrase.
#
# Two separate grammars, switched dynamically (see _switch_to_wake_grammar
# / _switch_to_command_grammar below), narrow the search further
# depending on context: while idle, Vosk only needs to recognize "hey
# sage" (plus the combined wake+command phrases, for saying it all in
# one breath); once a command window is open, it only needs to
# distinguish between the handful of known commands. Each grammar is a
# strict subset of the other's vocabulary, so this is strictly narrower
# than always using one combined list.
WAKE_GRAMMAR = json.dumps([
    "hey sage",
    "hey sage switch to hand tracking",
    "hey sage switch to head tracking",
    "hey sage recalibrate",
    "hey sage calibrate",
    "hey sage explain",
    # 13 September 2026 addition -- 5 new intents, one-breath combined
    # phrasing, matching the existing "hey sage <command>" pattern.
    "hey sage why is smartaccess useful",
    "hey sage how does smartaccess work",
    "hey sage what is adaptive sensitivity",
    "hey sage quit smartaccess",
    "hey sage tell me a joke",
    "hey sage tell me a smartaccess joke",
    "[unk]",
])

COMMAND_GRAMMAR = json.dumps([
    "switch to hand tracking",
    "switch to head tracking",
    "hand tracking",
    "head tracking",
    "recalibrate",
    "calibrate",
    "explain",
    "explain yourself",
    "what are you",
    "who are you",
    "how do you work",
    # 13 September 2026 addition -- split-style phrasing (said after
    # the wake window is already open) for the same 5 new intents.
    "why is smartaccess useful",
    "how does smartaccess work",
    "what is adaptive sensitivity",
    "quit smartaccess",
    "tell me a joke",
    "tell me a smartaccess joke",
    "[unk]",
])


class SageVoiceController:
    def __init__(self, engine, on_status=None, on_quit=None, model_path=None):
        """
        engine: the real TrackingEngine instance -- set_mode()/calibrate()
            are called directly on it. This class never imports the GUI,
            so it stays reusable from main.py's headless entry point too.
        on_status: optional callback(str), for a one-line status the GUI
            can display ("Listening...", "Heard: switch to hand
            tracking", etc.). Called from the LISTENER THREAD -- if the
            caller touches any GUI widget in this callback, it must hop
            back to the main thread itself (e.g. via CTk's self.after),
            the same rule this project already applies to hotkeys.
        on_quit: 13 September 2026 addition -- optional callback(), fired
            once the "quit" voice command's confirmation line (plus its
            echo tail) has FULLY finished playing -- never before. This
            class still never imports the GUI or calls sys.exit() itself;
            it just tells the caller "the quit line is done, it's safe to
            shut down now", the same arm's-length pattern as on_status.
            Called from the LISTENER THREAD -- same main-thread-hop rule
            as on_status applies here too.
        model_path: override for the Vosk model directory. Defaults to
            VOSK_MODEL_PATH above.
        """
        self.engine = engine
        self._on_status = on_status or (lambda msg: None)
        self._on_quit = on_quit or (lambda: None)
        self._quit_pending = False

        pygame.mixer.init()

        resolved_model_path = Path(model_path) if model_path else VOSK_MODEL_PATH
        if not resolved_model_path.exists():
            raise FileNotFoundError(
                f"Vosk model not found at {resolved_model_path}. Download it per "
                f"documentation/voice_lines.md step 4 before enabling voice commands."
            )
        print(f"[sage] Loading Vosk model from {resolved_model_path} ...")
        self._model = vosk.Model(str(resolved_model_path))
        self._recognizer = vosk.KaldiRecognizer(self._model, 16000, WAKE_GRAMMAR)
        print("[sage] Vosk model loaded OK (grammar-restricted to wake phrases)")

        self._audio_queue = queue.Queue()
        self._stream = None
        self._thread = None
        self._running = False
        self._awaiting_command_until = 0.0

        # Debug-only: lets the audio callback print "first chunk received"
        # exactly once instead of flooding the console every ~0.5s.
        self._debug_first_chunk_logged = False
        # Debug-only: tracks the last partial-recognition string we
        # printed, so we only print when it actually changes.
        self._debug_last_partial = ""

        # Feedback-loop suppression state (see ECHO_TAIL_S above).
        # _suppress_until is a timestamp: while time.time() is before it,
        # incoming audio is discarded rather than fed to the recognizer.
        self._suppress_until = 0.0
        self._debug_suppression_logged = False

        # 13 September 2026 addition -- lets the ack line run long. The
        # command window used to start counting the instant "hey sage"
        # was heard, which only worked because sage_ack.mp3 was meant to
        # be near-instant ("Yeah?"). Once that line is longer (e.g. "Did
        # someone say my name? ... What can I help you with?"), most of
        # the window would burn away while SAGE is still talking and
        # mic input is suppressed anyway. Instead: when the wake phrase
        # is heard, arm this flag rather than starting the timer
        # immediately; the timer only actually starts once suppression
        # ends (ack line + echo tail fully finished), so the user always
        # gets the full COMMAND_WINDOW_S after SAGE stops talking,
        # regardless of how long the ack line runs.
        self._arm_command_window_after_speech = False

        # Tracks which grammar is currently active, purely so the switch
        # helpers below only print when something actually changes.
        self._in_command_grammar = False

    # ------------------------------------------------------------
    # Playback
    # ------------------------------------------------------------

    def play_line(self, name):
        path = VOICE_ASSETS_DIR / VOICE_LINES[name]
        if not path.exists():
            print(f"[sage] Missing voice clip: {path} (skipping playback)")
            return
        pygame.mixer.music.load(str(path))
        pygame.mixer.music.play()
        # Force an immediate suppression window even before the listener
        # loop's next iteration notices get_busy()==True -- without this,
        # a chunk queued in the brief gap between .play() returning and
        # the loop's next check could still slip through and get
        # transcribed as the very start of SAGE's own line.
        self._suppress_until = time.time() + ECHO_TAIL_S

    def _is_sage_speaking(self):
        """True while a voice line is actively playing, or for a short
        ECHO_TAIL_S grace period after it finishes. While true, the
        listener loop discards incoming audio instead of feeding it to
        Vosk -- see ECHO_TAIL_S comment above for why this exists."""
        if pygame.mixer.music.get_busy():
            self._suppress_until = time.time() + ECHO_TAIL_S
            if not self._debug_suppression_logged:
                print("[sage] Suppressing mic input while a SAGE voice line plays")
                self._debug_suppression_logged = True
            return True
        if time.time() < self._suppress_until:
            return True
        if self._debug_suppression_logged:
            print("[sage] Resuming mic input -- playback + echo tail finished")
            self._debug_suppression_logged = False
            # 13 September 2026 addition -- quit. Checked BEFORE the
            # command-window arm below: sage_quit.mp3 finishing playback
            # (+ echo tail) is exactly the "safe to shut down now" point
            # the on_quit docstring promises, and there's no command
            # window to open afterward since the app is closing.
            if self._quit_pending:
                self._quit_pending = False
                print("[sage] Quit line finished -- signaling shutdown")
                self._on_quit()
            elif self._arm_command_window_after_speech:
                self._awaiting_command_until = time.time() + COMMAND_WINDOW_S
                self._arm_command_window_after_speech = False
                print(f"[sage] Command window now open for {COMMAND_WINDOW_S}s")
        return False

    def _switch_to_command_grammar(self):
        if not self._in_command_grammar:
            print("[sage] Narrowing recognizer grammar to commands only")
            self._recognizer.SetGrammar(COMMAND_GRAMMAR)
            self._in_command_grammar = True

    def _switch_to_wake_grammar(self):
        if self._in_command_grammar:
            print("[sage] Widening recognizer grammar back to wake phrases")
            self._recognizer.SetGrammar(WAKE_GRAMMAR)
            self._in_command_grammar = False

    def _check_command_window_expiry(self):
        """Catches the case where the command window opens but the user
        never says anything at all -- e.g. silence, or noise that never
        even reaches Vosk's final-result stage. Without this, a window
        that expires in pure silence would leave the recognizer stuck
        in COMMAND_GRAMMAR (which can't hear "hey sage" again) until the
        next stray sound happened to trigger a final result."""
        if self._awaiting_command_until and time.time() > self._awaiting_command_until:
            print("[sage] Command window closed -- no command heard")
            self._awaiting_command_until = 0.0
            self._switch_to_wake_grammar()

    # ------------------------------------------------------------
    # Listening lifecycle
    # ------------------------------------------------------------

    def start(self):
        print("[sage] start() called")
        if self._running:
            print("[sage] start() ignored -- already running")
            return

        print("[sage] Opening microphone stream (sounddevice.RawInputStream)...")
        try:
            self._stream = sd.RawInputStream(
                samplerate=16000, blocksize=8000, dtype="int16",
                channels=1, callback=self._audio_callback,
            )
            self._stream.start()
        except Exception as e:
            print(f"[sage] FAILED to open microphone: {e}")
            self.play_line("no_mic")
            self._on_status("No microphone detected -- voice commands off")
            return
        print("[sage] Microphone stream opened successfully")

        # Flush any audio chunks left over from before the previous
        # stop() -- otherwise the new listener thread would start by
        # processing stale audio through a freshly-reset recognizer.
        while not self._audio_queue.empty():
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

        self._debug_first_chunk_logged = False
        self._debug_last_partial = ""
        self._suppress_until = 0.0
        self._debug_suppression_logged = False
        self._arm_command_window_after_speech = False
        self._awaiting_command_until = 0.0
        self._recognizer.SetGrammar(WAKE_GRAMMAR)
        self._in_command_grammar = False

        self._running = True
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()
        print("[sage] Listener thread started")
        self._on_status('Listening for "Hey SAGE"...')

    def stop(self):
        print("[sage] stop() called")
        self._running = False
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        # 13 September 2026 fix -- CRASH. Root cause: this method used
        # to return immediately after flipping _running to False,
        # without waiting for the listener thread to actually exit.
        # That thread blocks on self._audio_queue.get(timeout=0.5) and
        # only notices _running == False on its NEXT loop iteration --
        # up to 0.5s later -- so it could still be mid-flight inside
        # self._recognizer.AcceptWaveform(...) when stop() returned.
        # If the user then re-toggled voice on quickly, start() would
        # call self._recognizer.SetGrammar(...) and spin up a SECOND
        # listener thread while the first one was still alive and
        # touching the same vosk.KaldiRecognizer object. KaldiRecognizer
        # is a thin wrapper over a C++ object and is NOT thread-safe --
        # two threads calling into it at once caused a silent native
        # segfault (no Python traceback, console just returns to the
        # prompt), which matches exactly what was observed. Joining here
        # guarantees the old thread has fully exited before stop()
        # returns, so start() can never overlap with a stale one. The
        # timeout is just a safety net (max ~0.5s wait, matching the
        # queue.get timeout above) in case the thread is stuck for an
        # unrelated reason -- it should always finish well before that.
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            if self._thread.is_alive():
                print("[sage] WARNING: listener thread did not exit within 1.0s of stop()")
            self._thread = None

    def _audio_callback(self, indata, frames, time_info, status):
        if not self._debug_first_chunk_logged:
            print("[sage] First audio chunk received from mic -- audio IS flowing in")
            self._debug_first_chunk_logged = True
        if status:
            # sounddevice reports overflow/underflow warnings here -- these
            # are worth seeing since a starved stream would explain "no
            # recognition ever happens" even though audio nominally arrives.
            print(f"[sage] Audio callback status flag: {status}")
        self._audio_queue.put(bytes(indata))

    def _listen_loop(self):
        print("[sage] Listener loop running -- waiting for audio chunks...")
        while self._running:
            try:
                data = self._audio_queue.get(timeout=0.5)
            except queue.Empty:
                self._check_command_window_expiry()
                continue

            if self._is_sage_speaking():
                # Discard this chunk entirely -- don't feed it to Vosk.
                # This is what stops SAGE's own confirmation/fallback
                # lines from being transcribed as if the user said them.
                self._debug_last_partial = ""
                continue

            if not self._recognizer.AcceptWaveform(data):
                # No FINAL result yet, but check the PARTIAL (in-progress)
                # transcript -- this is the key diagnostic: if partials
                # never show any words, Vosk is receiving audio but not
                # transcribing speech from it (mic gain, wrong input
                # device, background noise, etc.). If partials DO show
                # words but finals never trigger the wake check, the
                # issue is elsewhere.
                partial = json.loads(self._recognizer.PartialResult()).get("partial", "")
                if partial and partial != self._debug_last_partial:
                    print(f"[sage] (partial) hearing: '{partial}'")
                    self._debug_last_partial = partial
                continue

            result = json.loads(self._recognizer.Result())
            text = result.get("text", "").strip().lower()
            self._debug_last_partial = ""
            if not text:
                continue

            print(f"[sage] Recognized final text: '{text}'")

            now = time.time()
            if WAKE_PHRASE in text:
                print("[sage] WAKE PHRASE matched")
                self.play_line("ack")
                self._on_status('Heard "Hey SAGE" -- listening for a command...')
                # If wake phrase + command arrived in one utterance
                # ("hey sage switch to hand tracking"), handle the
                # remainder immediately instead of waiting on more audio
                # or on the ack line finishing -- it's already right here.
                remainder = text.split(WAKE_PHRASE, 1)[1].strip()
                if remainder:
                    self._handle_command(remainder)
                else:
                    # No command in the same breath -- open the command
                    # window once the ack line (and its echo tail) is
                    # done playing, not right now. See
                    # _arm_command_window_after_speech's docstring above.
                    self._arm_command_window_after_speech = True
                    self._switch_to_command_grammar()
                continue

            if now <= self._awaiting_command_until:
                self._handle_command(text)
            else:
                self._check_command_window_expiry()

    def _handle_command(self, text):
        print(f"[sage] Handling command text: '{text}'")
        self._awaiting_command_until = 0.0
        for intent, keywords in COMMAND_KEYWORDS.items():
            if any(kw in text for kw in keywords):
                self._dispatch(intent)
                self._switch_to_wake_grammar()
                return
        print(f"[sage] No keyword matched in: '{text}'")
        self._on_status(f'Didn\'t catch that: "{text}"')
        self.play_line("fallback")
        self._switch_to_wake_grammar()

    def _dispatch(self, intent):
        print(f"[sage] Dispatching intent: {intent}")
        self._on_status(f"SAGE: {intent.replace('_', ' ')}")
        if intent == "switch_hand":
            self.engine.set_mode(MODE_HAND)
            self.play_line("switch_hand")
        elif intent == "switch_head":
            self.engine.set_mode(MODE_HEAD)
            self.play_line("switch_head")
        elif intent == "recalibrate":
            self.play_line("recalibrate")
            self.engine.calibrate()
        elif intent == "explain":
            self.play_line("explain")
        elif intent == "explain_usefulness":
            self.play_line("explain_usefulness")
        elif intent == "explain_how_it_works":
            self.play_line("explain_how_it_works")
        elif intent == "explain_adaptive_sensitivity":
            self.play_line("explain_adaptive_sensitivity")
        elif intent == "tell_joke":
            self.play_line("tell_joke")
        elif intent == "quit":
            # 13 September 2026 addition. Stop accepting new commands
            # immediately -- _awaiting_command_until was already zeroed
            # by _handle_command() just above, and _quit_pending being
            # True means play_line("quit") below is the last thing this
            # controller will ever act on. Do NOT call self._on_quit()
            # here directly -- that would shut the app down while
            # sage_quit.mp3 is still playing. _quit_pending instead
            # defers the callback to _is_sage_speaking() above, which
            # only fires it once suppression naturally lifts, i.e. once
            # playback + the echo tail are fully done.
            self._quit_pending = True
            self.play_line("quit")
