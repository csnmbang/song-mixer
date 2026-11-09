#!/bin/zsh
# Double-click to open Song Mixer. Close this window to stop it.
cd "$(dirname "$0")"
PORT=5055
URL="http://127.0.0.1:$PORT"

if [[ ! -x .venv/bin/python ]]; then
  echo "Song Mixer isn't installed yet. Run ./setup.sh first."
  read -k1 "?Press any key to close."
  exit 1
fi

if curl -s -o /dev/null "$URL/api/status"; then
  open "$URL"
  exit 0
fi

echo "♪ Starting Song Mixer… (keep this window open while you use it)"
.venv/bin/python app.py &
SERVER=$!
trap "kill $SERVER 2>/dev/null" EXIT
for i in {1..60}; do
  curl -s -o /dev/null "$URL/api/status" && break
  sleep 0.5
done
open "$URL"
wait $SERVER
