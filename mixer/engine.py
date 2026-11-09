"""Audio engine: song analysis, vocal separation, and the recipe runner.

A recipe is a list of steps. Each step reads one or two named tracks and writes
a new named track. Songs start out as tracks "A" and "B". The last step's
output is the final mix.
"""

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import librosa
import numpy as np
import pyrubberband as pyrb
import soundfile as sf

SR = 44100
DATA = Path(__file__).resolve().parent.parent / "data"
UPLOADS = DATA / "uploads"
STEMS = DATA / "stems"
OUTPUTS = DATA / "outputs"
for d in (UPLOADS, STEMS, OUTPUTS):
    d.mkdir(parents=True, exist_ok=True)

DEMUCS_MODEL = "htdemucs_ft"

KEY_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
# Krumhansl-Schmuckler key profiles
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])


class RecipeError(Exception):
    pass


# ---------- songs ----------

def save_upload(filename: str, data: bytes) -> str:
    """Store an uploaded file under its content hash; return the song id."""
    song_id = hashlib.sha1(data).hexdigest()[:12]
    ext = Path(filename).suffix.lower() or ".mp3"
    path = UPLOADS / f"{song_id}{ext}"
    if not path.exists():
        path.write_bytes(data)
    meta_path = UPLOADS / f"{song_id}.json"
    if not meta_path.exists():
        meta = {"id": song_id, "name": clean_name(Path(filename).stem), "file": path.name}
        meta.update(analyze(load(path)))
        meta_path.write_text(json.dumps(meta))
    return song_id


def clean_name(stem: str) -> str:
    """Drop download-site clutter like "(320 Kbps)" or "[Official Video]"."""
    stem = re.sub(r"[\(\[][^\)\]]*(kbps|official|lyrics?|audio|video|hq|hd)[^\)\]]*[\)\]]", "", stem, flags=re.I)
    stem = re.sub(r"[_]+", " ", stem)
    return re.sub(r"\s{2,}", " ", stem).strip(" -") or "Untitled"


def song_meta(song_id: str) -> dict:
    path = UPLOADS / f"{song_id}.json"
    if not path.exists():
        raise RecipeError(f"Unknown song {song_id}")
    return json.loads(path.read_text())


def song_path(song_id: str) -> Path:
    return UPLOADS / song_meta(song_id)["file"]


def load(path: Path) -> np.ndarray:
    """Load any audio file as float32 stereo, shape (samples, 2)."""
    y, _ = librosa.load(str(path), sr=SR, mono=False)
    if y.ndim == 1:
        y = np.stack([y, y])
    return np.ascontiguousarray(y[:2].T, dtype=np.float32)


# ---------- analysis ----------

def analyze(y: np.ndarray) -> dict:
    mono = librosa.resample(y.mean(axis=1), orig_sr=SR, target_sr=22050)
    tempo, _ = librosa.beat.beat_track(y=mono, sr=22050)
    tonic, mode = detect_key(mono, 22050)
    bpm = float(np.atleast_1d(tempo)[0])
    while bpm > 160:  # beat trackers often lock onto double time
        bpm /= 2
    while 0 < bpm < 70:
        bpm *= 2
    return {
        "duration": round(len(y) / SR, 1),
        "bpm": round(bpm, 1),
        "key": f"{KEY_NAMES[tonic]} {mode}",
    }


def detect_key(mono: np.ndarray, sr: int) -> tuple[int, str]:
    chroma = librosa.feature.chroma_cqt(y=mono, sr=sr).mean(axis=1)
    best = (-2.0, 0, "major")
    for tonic in range(12):
        for mode, profile in (("major", MAJOR), ("minor", MINOR)):
            score = np.corrcoef(chroma, np.roll(profile, tonic))[0, 1]
            if score > best[0]:
                best = (score, tonic, mode)
    return best[1], best[2]


def key_to_pitch_class(key: str) -> int:
    """Map a key to its relative-major tonic so major and minor keys compare."""
    name, mode = key.split()
    pc = KEY_NAMES.index(name)
    return (pc + 3) % 12 if mode == "minor" else pc


def semitones_between(src_key: str, dst_key: str) -> int:
    diff = (key_to_pitch_class(dst_key) - key_to_pitch_class(src_key)) % 12
    return diff - 12 if diff > 6 else diff


def tempo_ratio(src_bpm: float, dst_bpm: float) -> float:
    """Stretch ratio that brings src to dst, allowing half/double time."""
    candidates = [dst_bpm * m / src_bpm for m in (0.5, 1, 2)]
    return min(candidates, key=lambda r: abs(np.log(r)))


# ---------- separation ----------

def stems(song_id: str) -> dict[str, Path]:
    """Split a song into vocals / instrumental with Demucs (cached per song)."""
    out = STEMS / DEMUCS_MODEL / song_id
    vocals, inst = out / "vocals.wav", out / "no_vocals.wav"
    if not (vocals.exists() and inst.exists()):
        src = song_path(song_id)
        cmd = [sys.executable, "-m", "demucs", "--two-stems=vocals", "-n", DEMUCS_MODEL,
               "-o", str(STEMS), "--filename", "{track}/{stem}.{ext}", str(src)]
        for device in ("mps", "cpu"):
            r = subprocess.run(cmd + ["-d", device], capture_output=True, text=True)
            if r.returncode == 0:
                break
        else:
            raise RecipeError(f"Vocal separation failed: {r.stderr[-400:]}")
        # Demucs names the folder after the file stem, which is the song id
    return {"vocals": vocals, "instrumental": inst}


# ---------- recipe ----------

OPS = {
    "separate": "Keep only the vocals or only the instrumental of a track",
    "trim": "Cut a track to start..end seconds",
    "tempo": "Change speed by a ratio (1.1 = 10% faster) without changing pitch",
    "match_tempo": "Stretch a track so its BPM matches another track",
    "pitch": "Shift key up/down by semitones without changing speed",
    "match_key": "Shift a track's key to match another track",
    "gain": "Change volume in dB (+3 louder, -6 quieter)",
    "fade": "Fade in and/or fade out, in seconds",
    "loop": "Repeat a track N times",
    "overlay": "Play two tracks at the same time (second starts at offset seconds)",
    "concat": "Play one track after the other, optionally crossfading",
}


def describe(step: dict, names: dict[str, str] | None = None) -> str:
    names = names or {}
    op = step["op"]
    a = names.get(step.get("input"), step.get("input"))
    b = names.get(step.get("input2"), step.get("input2"))
    if op == "separate":
        return f"Pull out the {step.get('part')} from {a}"
    if op == "trim":
        return f"Cut {a} to {fmt_t(step.get('start') or 0)}–{fmt_t(step.get('end')) if step.get('end') else 'end'}"
    if op == "tempo":
        return f"Make {a} {abs((step.get('ratio') or 1) - 1):.0%} {'faster' if (step.get('ratio') or 1) >= 1 else 'slower'}"
    if op == "match_tempo":
        return f"Match the tempo of {a} to {b}"
    if op == "pitch":
        s = step.get("semitones") or 0
        return f"Shift {a} {'up' if s >= 0 else 'down'} {abs(s)} semitone{'s' if abs(s) != 1 else ''}"
    if op == "match_key":
        return f"Match the key of {a} to {b}"
    if op == "gain":
        db = step.get("gain_db") or 0
        return f"Make {a} {'louder' if db >= 0 else 'quieter'} ({db:+g} dB)"
    if op == "fade":
        fi, fo = step.get("fade_in") or 0, step.get("fade_out") or 0
        parts = ([f"in over {fi:g}s"] if fi else []) + ([f"out over {fo:g}s"] if fo else [])
        return f"Fade {a} " + " and ".join(parts or ["(no change)"])
    if op == "loop":
        return f"Repeat {a} {step.get('times')}×"
    if op == "overlay":
        return f"Layer {b} on top of {a}" + (f" from {fmt_t(step['offset'])}" if step.get("offset") else "")
    if op == "concat":
        return f"Play {a} then {b}" + (f" with a {step['crossfade']}s crossfade" if step.get("crossfade") else "")
    return op


def fmt_t(sec) -> str:
    sec = float(sec)
    rest = sec % 60
    return f"{int(sec // 60)}:{rest:02.0f}" if rest == int(rest) else f"{int(sec // 60)}:{rest:04.1f}"


def possessive(name: str) -> str:
    return f"{name}\u2019" if name.endswith("s") else f"{name}\u2019s"


class Runner:
    """Executes a recipe step by step, reporting progress through `on_step`."""

    def __init__(self, songs: dict[str, str], on_step=lambda i, status: None):
        self.songs = songs  # {"A": song_id, "B": song_id}
        self.tracks: dict[str, np.ndarray] = {}
        self.meta: dict[str, dict] = {}
        self.on_step = on_step

    def track(self, name: str | None) -> np.ndarray:
        if not name:
            raise RecipeError("A step is missing its input track")
        if name not in self.tracks:
            if name in self.songs:
                self.tracks[name] = load(song_path(self.songs[name]))
                self.meta[name] = song_meta(self.songs[name])
            else:
                raise RecipeError(f"Track '{name}' doesn't exist yet")
        return self.tracks[name]

    def info(self, name: str) -> dict:
        self.track(name)
        return self.meta[name]

    def run(self, steps: list[dict]) -> np.ndarray:
        if not steps:
            raise RecipeError("The recipe has no steps")
        out = None
        for i, step in enumerate(steps):
            self.on_step(i, "running")
            out = step.get("output") or f"step{i + 1}"
            y, meta = self.apply(step)
            self.tracks[out], self.meta[out] = y, meta
            self.on_step(i, "done")
        return self.tracks[out]

    def apply(self, s: dict) -> tuple[np.ndarray, dict]:
        op = s["op"]
        a = s.get("input")
        if op == "separate":
            part = s.get("part") or "instrumental"
            if a not in self.songs:
                raise RecipeError("Vocals can only be separated from an original song (A or B)")
            y = load(stems(self.songs[a])[part])
            return y, dict(self.info(a))
        y, meta = self.track(a), dict(self.info(a))
        if op == "trim":
            start = int((s.get("start") or 0) * SR)
            end = int(s["end"] * SR) if s.get("end") else len(y)
            return y[start:end], meta
        if op == "tempo":
            ratio = float(s.get("ratio") or 1)
            meta["bpm"] = round(meta["bpm"] * ratio, 1)
            return stretch(y, ratio), meta
        if op == "match_tempo":
            ratio = tempo_ratio(meta["bpm"], self.info(s.get("input2"))["bpm"])
            meta["bpm"] = round(meta["bpm"] * ratio, 1)
            return stretch(y, ratio), meta
        if op == "pitch":
            return shift(y, int(s.get("semitones") or 0)), meta
        if op == "match_key":
            target = self.info(s.get("input2"))["key"]
            n = semitones_between(meta["key"], target)
            meta["key"] = target
            return shift(y, n), meta
        if op == "gain":
            return y * (10 ** ((s.get("gain_db") or 0) / 20)), meta
        if op == "fade":
            return fade(y, s.get("fade_in") or 0, s.get("fade_out") or 0), meta
        if op == "loop":
            return np.tile(y, (max(1, int(s.get("times") or 1)), 1)), meta
        if op == "overlay":
            b = self.track(s.get("input2"))
            off = int((s.get("offset") or 0) * SR)
            n = max(len(y), off + len(b))
            mix = np.zeros((n, 2), np.float32)
            mix[: len(y)] += y
            mix[off: off + len(b)] += b
            return mix, meta
        if op == "concat":
            b = self.track(s.get("input2"))
            xf = min(int((s.get("crossfade") or 0) * SR), len(y), len(b))
            if xf == 0:
                return np.concatenate([y, b]), meta
            ramp = np.linspace(0, 1, xf, dtype=np.float32)[:, None]
            mid = y[-xf:] * (1 - ramp) + b[:xf] * ramp
            return np.concatenate([y[:-xf], mid, b[xf:]]), meta
        raise RecipeError(f"Unknown step '{op}'")


def stretch(y: np.ndarray, ratio: float) -> np.ndarray:
    if abs(ratio - 1) < 1e-3:
        return y
    return pyrb.time_stretch(y, SR, ratio).astype(np.float32)


def shift(y: np.ndarray, semitones: int) -> np.ndarray:
    if semitones == 0:
        return y
    return pyrb.pitch_shift(y, SR, semitones).astype(np.float32)


def fade(y: np.ndarray, fade_in: float, fade_out: float) -> np.ndarray:
    y = y.copy()
    fi, fo = min(int(fade_in * SR), len(y)), min(int(fade_out * SR), len(y))
    if fi:
        y[:fi] *= np.linspace(0, 1, fi, dtype=np.float32)[:, None]
    if fo:
        y[-fo:] *= np.linspace(1, 0, fo, dtype=np.float32)[:, None]
    return y


def export(y: np.ndarray, name: str) -> Path:
    """Normalize to avoid clipping and write a 320 kbps MP3."""
    peak = float(np.abs(y).max() or 1)
    if peak > 0.98:
        y = y * (0.98 / peak)
    wav = OUTPUTS / f"{name}.wav"
    mp3 = OUTPUTS / f"{name}.mp3"
    sf.write(wav, y, SR)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-b:a", "320k", str(mp3)],
                   check=True)
    wav.unlink()
    return mp3
