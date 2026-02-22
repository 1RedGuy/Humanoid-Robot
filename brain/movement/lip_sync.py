"""
Lip-sync controller — syllable-based viseme animation for jaw and upper lip.

Segments ElevenLabs character-level alignment data into syllable-like chunks,
maps each to a viseme (mouth shape), and drives servos through a dedicated
mixer layer synchronised with audio playback.

Viseme definitions (6 mouth shapes):
    CLOSED  — jaw closed, lip neutral    (spaces, punctuation, m/b/p-only)
    SMALL   — jaw slightly open           (consonant-only segments)
    MEDIUM  — jaw medium open             (vowels e, i, y)
    WIDE    — jaw fully open              (vowel a)
    ROUND   — jaw medium, lip raised      (vowels o, u)
    FV      — jaw barely open, lip raised (f/v-dominated segments)

jaw_openness (0-1) interpolates between jaw_closed and jaw_open angles.
lip_raise    (0-1) interpolates between upper_lip_neutral and upper_lip_raised.
"""

import threading
import time
from typing import Dict, List, Optional, Tuple

from brain.movement.servo_mixer import ServoMixer

LIP_SYNC_LAYER = "lip_sync"
LIP_SYNC_PRIORITY = 7

# Latin vowels (English, etc.)
VOWELS_LATIN = set("aeiouyAEIOUY")
# Cyrillic vowels (Bulgarian, etc.): а, е, и, о, у, ю, я, ъ (and uppercase)
VOWELS_CYRILLIC = set("аеиоуюяъАЕИОУЮЯЪ")
VOWELS = VOWELS_LATIN | VOWELS_CYRILLIC

# Consonants that imply closed mouth (bilabials)
BILABIALS = set("mbpMBP") | set("мбпМБП")
# Consonants that imply FV viseme
FV_CHARS = set("fvFV") | set("вфВФ")

_CHAR_TO_VISEME = {
    # Latin
    "a": "WIDE", "A": "WIDE",
    "e": "MEDIUM", "E": "MEDIUM",
    "i": "MEDIUM", "I": "MEDIUM",
    "o": "ROUND", "O": "ROUND",
    "u": "ROUND", "U": "ROUND",
    "y": "MEDIUM", "Y": "MEDIUM",
    # Cyrillic (Bulgarian)
    "а": "WIDE", "А": "WIDE",
    "е": "MEDIUM", "Е": "MEDIUM",
    "и": "MEDIUM", "И": "MEDIUM",
    "о": "ROUND", "О": "ROUND",
    "у": "ROUND", "У": "ROUND",
    "ю": "ROUND", "Ю": "ROUND",
    "я": "WIDE", "Я": "WIDE",   # /ja/ — open
    "ъ": "MEDIUM", "Ъ": "MEDIUM",  # Bulgarian schwa
}


def _classify(ch: str) -> str:
    """Classify a single character as 'vowel', 'consonant', or 'other'."""
    if ch in VOWELS:
        return "vowel"
    if ch.isalpha():
        return "consonant"
    return "other"


def _segment_into_syllables(
    characters: List[str],
    starts: List[float],
    ends: List[float],
) -> List[dict]:
    """
    Group characters into syllable-like segments.

    Each segment dict:
        chars: list of characters
        starts: list of start times (parallel to chars)
        ends: list of end times (parallel to chars)
        vowel_chars: list of characters forming the vowel nucleus
        vowel_start: start time of the first vowel (or segment start)
    """
    segments: List[dict] = []
    current: Optional[dict] = None
    found_vowel_in_current = False

    for i, ch in enumerate(characters):
        kind = _classify(ch)

        if kind == "other":
            if current and current["chars"]:
                segments.append(current)
            segments.append({
                "chars": [ch],
                "starts": [starts[i]],
                "ends": [ends[i]],
                "vowel_chars": [],
                "vowel_start": starts[i],
            })
            current = None
            found_vowel_in_current = False
            continue

        if kind == "vowel":
            if current is None:
                current = {
                    "chars": [], "starts": [], "ends": [],
                    "vowel_chars": [], "vowel_start": starts[i],
                }
                found_vowel_in_current = False

            if found_vowel_in_current and not _classify(characters[i - 1]) == "vowel":
                # New vowel after consonant(s) in the coda — start new syllable.
                # Re-attach trailing consonants after the last vowel run as the
                # onset of this new syllable.
                onset_chars = []
                onset_starts = []
                onset_ends = []
                while current["chars"] and _classify(current["chars"][-1]) == "consonant":
                    onset_chars.insert(0, current["chars"].pop())
                    onset_starts.insert(0, current["starts"].pop())
                    onset_ends.insert(0, current["ends"].pop())
                if current["chars"]:
                    segments.append(current)
                current = {
                    "chars": onset_chars,
                    "starts": onset_starts,
                    "ends": onset_ends,
                    "vowel_chars": [],
                    "vowel_start": starts[i],
                }
                found_vowel_in_current = False

            current["chars"].append(ch)
            current["starts"].append(starts[i])
            current["ends"].append(ends[i])
            if not found_vowel_in_current:
                current["vowel_start"] = starts[i]
            current["vowel_chars"].append(ch)
            found_vowel_in_current = True

        else:  # consonant
            if current is None:
                current = {
                    "chars": [], "starts": [], "ends": [],
                    "vowel_chars": [], "vowel_start": starts[i],
                }
                found_vowel_in_current = False
            current["chars"].append(ch)
            current["starts"].append(starts[i])
            current["ends"].append(ends[i])

    if current and current["chars"]:
        segments.append(current)

    return segments


def _viseme_for_segment(seg: dict) -> str:
    """Pick a viseme for a syllable segment based on its vowel nucleus."""
    if not seg["chars"][0].isalpha():
        return "CLOSED"

    if not seg["vowel_chars"]:
        all_chars = "".join(seg["chars"])
        if all(c in BILABIALS for c in all_chars):
            return "CLOSED"
        if any(c in FV_CHARS for c in all_chars):
            return "FV"
        return "SMALL"

    first_vowel = seg["vowel_chars"][0]
    return _CHAR_TO_VISEME.get(first_vowel, "MEDIUM")


def _viseme_to_angles(
    viseme: str,
    config: dict,
) -> Dict[str, float]:
    """Convert a viseme name to concrete servo angles using the config."""
    viseme_def = config.get("visemes", {}).get(viseme)
    if viseme_def is None:
        viseme_def = {"jaw_openness": 0.0, "lip_raise": 0.0}

    openness = float(viseme_def.get("jaw_openness", 0.0))
    lip_raise = float(viseme_def.get("lip_raise", 0.0))

    jaw_closed = config.get("jaw_closed", {})
    jaw_open = config.get("jaw_open", {})
    lip_neutral = float(config.get("upper_lip_neutral", 90))
    lip_raised = float(config.get("upper_lip_raised", 120))

    targets: Dict[str, float] = {}

    for servo_name in ("LeftJaw", "RightJaw"):
        closed_val = float(jaw_closed.get(servo_name, 90))
        open_val = float(jaw_open.get(servo_name, 90))
        targets[servo_name] = closed_val + (open_val - closed_val) * openness

    targets["UpperLip"] = lip_neutral + (lip_raised - lip_neutral) * lip_raise

    return targets


def build_viseme_timeline(
    alignment: dict,
    config: dict,
) -> List[Tuple[float, Dict[str, float]]]:
    """
    Build a timeline of (timestamp, {servo_name: angle}) keyframes from
    ElevenLabs character alignment data.

    Returns one keyframe per syllable-like segment, plus a final CLOSED.
    """
    characters = alignment.get("characters", [])
    starts = alignment.get("character_start_times_seconds", [])
    ends = alignment.get("character_end_times_seconds", [])

    if not characters or len(characters) != len(starts) or len(characters) != len(ends):
        return []

    segments = _segment_into_syllables(characters, starts, ends)
    if not segments:
        return []

    closed_angles = _viseme_to_angles("CLOSED", config)
    timeline: List[Tuple[float, Dict[str, float]]] = [(0.0, closed_angles)]
    prev_viseme = "CLOSED"

    for seg in segments:
        viseme = _viseme_for_segment(seg)
        t = seg["vowel_start"]

        if viseme == prev_viseme:
            continue
        prev_viseme = viseme
        timeline.append((t, _viseme_to_angles(viseme, config)))

    last_end = max(ends) if ends else 0.0
    if prev_viseme != "CLOSED":
        timeline.append((last_end, closed_angles))

    return timeline


class LipSyncController:
    """
    Drives jaw/upper-lip servos in sync with audio playback.

    Call ``start(alignment)`` right before playback begins and
    ``stop()`` after playback finishes.  The controller spawns a daemon
    thread that walks the viseme timeline and pushes keyframes into the
    ServoMixer's ``lip_sync`` layer.
    """

    def __init__(self, mixer: ServoMixer, config: dict):
        self._mixer = mixer
        self._config = config
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @property
    def enabled(self) -> bool:
        return bool(self._config.get("enabled", True))

    def start(self, alignment: dict):
        """Build timeline from alignment and begin playback in a background thread."""
        if not self.enabled:
            return
        if alignment is None:
            return

        timeline = build_viseme_timeline(alignment, self._config)
        if not timeline:
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._playback_loop,
            args=(timeline,),
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """Signal the playback thread to stop, close the mouth, then release the layer."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        try:
            closed = _viseme_to_angles("CLOSED", self._config)
            self._mixer.set_layer(LIP_SYNC_LAYER, LIP_SYNC_PRIORITY, closed, duration=0.1)
            time.sleep(0.15)
            self._mixer.release_layer(LIP_SYNC_LAYER, duration=0.15)
        except Exception as e:
            print(f"[LipSync] release error: {e}")

    def _playback_loop(self, timeline: List[Tuple[float, Dict[str, float]]]):
        """Walk the timeline keyframes, sleeping between them."""
        transition = float(self._config.get("transition_duration", 0.05))
        origin = time.monotonic()

        for ts, targets in timeline:
            if self._stop_event.is_set():
                return

            now = time.monotonic() - origin
            wait = ts - now
            if wait > 0:
                if self._stop_event.wait(timeout=wait):
                    return

            if self._stop_event.is_set():
                return

            try:
                self._mixer.set_layer(
                    LIP_SYNC_LAYER,
                    LIP_SYNC_PRIORITY,
                    targets,
                    duration=transition,
                )
            except Exception as e:
                print(f"[LipSync] set_layer error: {e}")
