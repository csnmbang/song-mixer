#!/bin/zsh
# One-time install: Python env, audio tools, and the Demucs model.
set -e
cd "$(dirname "$0")"
command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/"; exit 1; }
command -v ffmpeg >/dev/null || brew install ffmpeg
command -v rubberband >/dev/null || brew install rubberband
uv venv -q --python 3.11 .venv
uv pip install -q --python .venv/bin/python -r requirements.txt
[[ -f .env ]] || cp .env.example .env
echo "Done. Add your ANTHROPIC_API_KEY to .env, then double-click 'Song Mixer.command'."
