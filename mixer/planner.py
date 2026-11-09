"""Turn plain-English instructions into a recipe with Claude, plus no-AI presets."""

import json
import os

import anthropic

from .engine import OPS, describe, possessive

MODEL = "claude-opus-5-5"

NUM = {"type": ["number", "null"]}
STR = {"type": ["string", "null"]}
STEP_FIELDS = {
    "op": {"type": "string", "enum": list(OPS)},
    "input": {"type": "string"},
    "input2": STR,
    "output": {"type": "string"},
    "part": {"type": "string", "enum": ["vocals", "instrumental", "none"]},
    "start": NUM, "end": NUM, "ratio": NUM, "semitones": NUM, "gain_db": NUM,
    "fade_in": NUM, "fade_out": NUM, "times": NUM, "offset": NUM, "crossfade": NUM,
}
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "warnings": {"type": "array", "items": {"type": "string"}},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": STEP_FIELDS,
                "required": list(STEP_FIELDS),
                "additionalProperties": False,
            },
        },
    },
    "required": ["title", "summary", "warnings", "steps"],
    "additionalProperties": False,
}

SYSTEM = f"""You turn someone's plain-English request about editing or mashing up songs into a recipe for an audio tool. The person is not technical.

Songs are available as tracks named by letter ("A", "B", ...). Each step reads `input` (and `input2` for two-track steps), and writes a new track named `output`; later steps can use earlier outputs. The final step's output is the finished song.

Available steps:
{chr(10).join(f"- {k}: {v}" for k, v in OPS.items())}

Field use: separate→part; trim→start,end (seconds, end null = to the end); tempo→ratio; match_tempo/match_key→input2 is the reference track; pitch→semitones; gain→gain_db; fade→fade_in,fade_out; loop→times; overlay→input2 is layered on top, offset; concat→input2 plays after, crossfade. Set every field a step doesn't use to null (part: "none").

Guidelines:
- "Remove the lyrics/vocals/singing" means separate with part=instrumental.
- For a mashup (vocals of one song over another), separate the vocals and the instrumental, match the vocals' tempo and key to the instrumental, then overlay. Prefer moving the vocals rather than the instrumental.
- Use the BPM, key, and duration given for each song. Times like "1:30" mean 90 seconds.
- Add a warning when a tempo change is more than ~15%, a key shift is more than 3 semitones, or the request asks for something these steps can't do (then do the closest thing you can and say so).
- title: 2-5 cute words naming the result. summary: one friendly sentence on what you'll make.
- Keep it to the fewest steps that do what was asked."""


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def plan(prompt: str, songs: dict[str, dict], previous: dict | None = None) -> dict:
    song_lines = "\n".join(
        f"- {letter}: \"{m['name']}\" — {fmt_dur(m['duration'])}, {m['bpm']} BPM, {m['key']}"
        for letter, m in songs.items()
    )
    content = f"Songs:\n{song_lines}\n\n"
    if previous:
        content += ("This is a change to the previous recipe, which was:\n"
                    f"{json.dumps(previous['steps'])}\n\nChange it as asked:\n")
    content += f"Request: {prompt}"

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude couldn't help with that request.")
    text = next(b.text for b in response.content if b.type == "text")
    return finish(json.loads(text), songs)


def finish(recipe: dict, songs: dict[str, dict]) -> dict:
    """Check the recipe only references real tracks, and add readable step labels."""
    known = set(songs)
    names = {k: m["name"] for k, m in songs.items()}
    for i, step in enumerate(recipe["steps"]):
        for ref in (step.get("input"), step.get("input2")):
            if ref and ref not in known:
                raise RuntimeError(f"Step {i + 1} uses a track that doesn't exist ({ref}).")
        if step["op"] in ("overlay", "concat", "match_tempo", "match_key") and not step.get("input2"):
            raise RuntimeError(f"Step {i + 1} needs a second track.")
        if step.get("part") == "none":
            step["part"] = None
        step["output"] = step.get("output") or f"step{i + 1}"
        known.add(step["output"])
        step["label"] = describe(step, names)
        if step["op"] == "separate":
            names[step["output"]] = f"{possessive(names[step['input']])} {step['part']}"
        elif step["op"] in ("overlay", "concat"):
            names[step["output"]] = "the mix"
        else:
            names[step["output"]] = names[step["input"]]
    return recipe


def fmt_dur(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}"


def step(op, input, output, **kw):
    s = {k: None for k in STEP_FIELDS}
    s.update(op=op, input=input, output=output, **kw)
    return s


def preset(name: str, songs: dict[str, dict]) -> dict:
    if name == "instrumental":
        r = {"title": "Karaoke version", "summary": "The song with the vocals taken out.",
             "steps": [step("separate", "A", "final", part="instrumental")]}
    elif name == "vocals":
        r = {"title": "Just the voice", "summary": "Only the vocals, no instruments.",
             "steps": [step("separate", "A", "final", part="vocals")]}
    elif name == "join":
        r = {"title": "Back to back", "summary": "Song A, then song B, blended with a 4-second crossfade.",
             "steps": [step("concat", "A", "final", input2="B", crossfade=4)]}
    elif name == "mashup":
        r = {"title": "Little mashup",
             "summary": "Song B's vocals over song A's instrumental, matched in tempo and key.",
             "steps": [
                 step("separate", "A", "beat", part="instrumental"),
                 step("separate", "B", "voice", part="vocals"),
                 step("match_tempo", "voice", "voice_t", input2="A"),
                 step("match_key", "voice_t", "voice_k", input2="A"),
                 step("overlay", "beat", "final", input2="voice_k"),
             ]}
    else:
        raise RuntimeError(f"Unknown preset {name}")
    r["warnings"] = warnings_for(name, songs)
    return finish(r, songs)


def warnings_for(name: str, songs: dict[str, dict]) -> list[str]:
    from .engine import semitones_between, tempo_ratio
    if name != "mashup" or "B" not in songs:
        return []
    a, b = songs["A"], songs["B"]
    out = []
    ratio = tempo_ratio(b["bpm"], a["bpm"])
    if abs(ratio - 1) > 0.15:
        out.append(f"The vocals need to change speed by {abs(ratio - 1):.0%}, so they may sound a bit stretched.")
    n = semitones_between(b["key"], a["key"])
    if abs(n) > 3:
        out.append(f"The vocals move {abs(n)} semitones to match the key, which can sound a little robotic.")
    return out
