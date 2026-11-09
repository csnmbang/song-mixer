"""Song Mixer — a little local web app for removing vocals and making mashups."""

import threading
import time
import uuid

from dotenv import load_dotenv
from flask import Flask, abort, jsonify, request, send_from_directory

load_dotenv()

from mixer import engine, planner  # noqa: E402  (needs .env loaded first)

app = Flask(__name__, static_folder="static", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 200 * 1024 * 1024
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()  # one mix at a time; Demucs uses the whole GPU


def songs_from(body) -> dict[str, dict]:
    ids = body.get("songs") or {}
    if "A" not in ids:
        raise RuntimeError("Add a song first.")
    return {letter: engine.song_meta(sid) for letter, sid in ids.items() if sid}


@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/api/status")
def status():
    return jsonify(ai=planner.available())


@app.post("/api/upload")
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        abort(400)
    try:
        song_id = engine.save_upload(f.filename, f.read())
    except Exception as e:  # unreadable audio
        return jsonify(error=f"Couldn't read that file ({e.__class__.__name__})."), 400
    return jsonify(engine.song_meta(song_id))


@app.post("/api/plan")
def plan():
    body = request.get_json()
    try:
        songs = songs_from(body)
        if body.get("preset"):
            recipe = planner.preset(body["preset"], songs)
        else:
            if not planner.available():
                return jsonify(error="Typed instructions need an ANTHROPIC_API_KEY in .env. "
                                     "The quick buttons work without it."), 400
            prompt = (body.get("prompt") or "").strip()
            if not prompt:
                return jsonify(error="Tell me what to make first."), 400
            recipe = planner.plan(prompt, songs, body.get("previous"))
    except Exception as e:
        return jsonify(error=str(e)), 400
    return jsonify(recipe)


@app.post("/api/run")
def run():
    body = request.get_json()
    recipe = body["recipe"]
    job_id = uuid.uuid4().hex[:10]
    job = {"id": job_id, "state": "queued", "steps": ["pending"] * len(recipe["steps"]),
           "started": time.time(), "error": None, "file": None}
    JOBS[job_id] = job

    def on_step(i, state):
        job["steps"][i] = state

    def work():
        with LOCK:
            job["state"] = "running"
            job["started"] = time.time()
            try:
                y = engine.Runner(body["songs"], on_step).run(recipe["steps"])
                slug = "".join(c if c.isalnum() else "-" for c in recipe.get("title", "mix")).strip("-")
                path = engine.export(y, f"{slug or 'mix'}-{job_id}")
                job.update(state="done", file=path.name, duration=round(len(y) / engine.SR, 1))
            except Exception as e:
                job["steps"] = ["failed" if s == "running" else s for s in job["steps"]]
                job.update(state="failed", error=str(e))
            job["elapsed"] = round(time.time() - job["started"], 1)

    threading.Thread(target=work, daemon=True).start()
    return jsonify(job)


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = JOBS.get(job_id) or abort(404)
    return jsonify(job)


@app.get("/files/<path:name>")
def files(name):
    return send_from_directory(engine.OUTPUTS, name, as_attachment=request.args.get("dl") == "1")


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5055, threaded=True)
