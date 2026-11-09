# Song Mixer

A little local web app for removing vocals from songs and making mashups.
Drop in one or two songs, tap a shortcut or describe what you want, check the
plan, and press **Make it**.

## Using it

Double-click **`Song Mixer.command`**. A browser tab opens at
`http://127.0.0.1:5055`. Keep the small Terminal window open while you use it;
closing it stops the app.

- **Remove vocals / Vocals only**: works with one song.
- **Mashup / Join with crossfade**: needs two songs.
- **Typed instructions**, e.g. *"Put the singing from B over the music of A,
  starting at 0:20, and fade out at the end"*. Claude turns this into a plan
  before anything runs.
- **Tweak it**: after a mix, the suggestions (or anything you type) adjust the
  last plan instead of starting over.

Finished songs are saved in `data/outputs/`.

## How it works

1. On upload, the app measures each song's tempo, key and length (`librosa`).
2. Claude reads the instructions plus those measurements and returns a recipe
   built only from fixed steps: separate, trim, tempo, match tempo, pitch,
   match key, volume, fade, loop, layer, join. It never runs code.
3. The recipe runs locally: [Demucs](https://github.com/facebookresearch/demucs)
   (`htdemucs_ft`) splits vocals from music, Rubber Band changes tempo and key,
   and ffmpeg writes a 320 kbps MP3.

Vocal separation is cached per song, so only the first run on a song is slow
(about 1–2 minutes on an M-series Mac).

## Setup on a new Mac

```bash
./setup.sh
```

Then put an Anthropic API key in `.env` (`ANTHROPIC_API_KEY=...`). Without a
key the shortcut buttons still work; typed instructions are turned off.

## Limits

- Mashups sound best when the songs are within ~15% in tempo and ~3 semitones
  in key. The plan warns when they aren't.
- Vocal removal can leave faint traces of the voice on some songs.
- Tempo and key detection are estimates and can be off on unusual songs.
