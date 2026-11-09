const $ = (s, el = document) => el.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmt = (sec) => `${Math.floor(sec / 60)}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
const icon = {
  plus: '<svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg>',
  x: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></svg>',
  check: '<svg viewBox="0 0 24 24"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
  warn: '<svg viewBox="0 0 24 24"><path d="M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
  ret: '<svg viewBox="0 0 24 24"><path d="M9 10l-5 5 5 5"/><path d="M20 4v7a4 4 0 0 1-4 4H4"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="M12 4v12M6 11l6 6 6-6M5 20h14"/></svg>',
  spark: '<svg viewBox="0 0 24 24"><path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z"/></svg>',
};
const pixels = '<span class="pixels">' + "<i></i>".repeat(9) + "</span>";

const state = { songs: { A: null, B: null }, loading: {}, recipe: null, lastRecipe: null, ai: true };
const FOLLOWUPS = ["Make the vocals a little louder", "Start from the chorus instead", "Add a soft fade out at the end", "Make it about a minute long"];
const PLACEHOLDERS = [
  "Put the singing from B over the music of A…",
  "Remove the vocals, but only from 0:30 to the end…",
  "Join both songs with a long dreamy crossfade…",
  "Loop the first 20 seconds of A four times…",
];

/* ---------- theme ---------- */
function setTheme(t) {
  document.documentElement.dataset.theme = t;
  document.querySelectorAll("[data-theme-set]").forEach((b) => b.classList.toggle("on", b.dataset.themeSet === t));
  try { localStorage.setItem("theme", t); } catch {}
}
let saved = null;
try { saved = localStorage.getItem("theme"); } catch {}
setTheme(saved || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
document.querySelectorAll("[data-theme-set]").forEach((b) => (b.onclick = () => setTheme(b.dataset.themeSet)));

/* ---------- helpers ---------- */
function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove("show"), 3200);
}
async function api(path, body) {
  const res = await fetch(path, body instanceof FormData ? { method: "POST", body } : body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  const data = await res.json().catch(() => ({ error: "Something went wrong." }));
  if (!res.ok || data.error) throw new Error(data.error || "Something went wrong.");
  return data;
}
const songIds = () => Object.fromEntries(Object.entries(state.songs).filter(([, s]) => s).map(([k, s]) => [k, s.id]));
const songCount = () => Object.values(state.songs).filter(Boolean).length;
function show(id, on = true) { $(id).hidden = !on; }
function scrollTo(id) { setTimeout(() => $(id).scrollIntoView({ behavior: "smooth", block: "start" }), 50); }

/* ---------- songs ---------- */
function renderSlots() {
  document.querySelectorAll(".slot").forEach((slot) => {
    const L = slot.dataset.letter, s = state.songs[L];
    if (state.loading[L]) {
      slot.className = "slot filled";
      slot.innerHTML = `<div class="song-head"><div class="letter">${L}</div><div style="min-width:0"><div class="song-name">${esc(state.loading[L])}</div>
        <div class="thinking">${pixels}<span class="shimmer">Listening for tempo & key</span></div></div></div>`;
    } else if (s) {
      slot.className = "slot filled fade-in";
      slot.innerHTML = `<div class="song-head"><div class="letter">${L}</div><div style="min-width:0"><div class="song-name" title="${esc(s.name)}">${esc(s.name)}</div>
        <div class="song-sub">Song ${L}</div></div><button class="remove" aria-label="Remove song ${L}">${icon.x}</button></div>
        <div class="facts"><span class="fact">${fmt(s.duration)}</span><span class="fact">${s.bpm} BPM</span><span class="fact">${esc(s.key)}</span></div>`;
      $(".remove", slot).onclick = (e) => { e.stopPropagation(); state.songs[L] = null; renderSlots(); };
    } else {
      slot.className = "slot empty";
      slot.innerHTML = `<div><div class="plus">${icon.plus}</div><div>Drop song ${L}</div><div class="optional">${L === "A" ? "MP3, WAV, M4A…" : "optional, for mixing"}</div></div>`;
    }
  });
  const n = songCount();
  document.querySelectorAll("[data-preset]").forEach((b) => (b.disabled = n < +b.dataset.needs));
}

async function addSong(L, file) {
  state.loading[L] = file.name.replace(/\.[^.]+$/, "");
  renderSlots();
  const fd = new FormData();
  fd.append("file", file);
  try {
    state.songs[L] = await api("/api/upload", fd);
  } catch (e) {
    toast(e.message);
  }
  delete state.loading[L];
  renderSlots();
}

let pickFor = "A";
$("#file-input").onchange = (e) => { if (e.target.files[0]) addSong(pickFor, e.target.files[0]); e.target.value = ""; };
document.querySelectorAll(".slot").forEach((slot) => {
  const L = slot.dataset.letter;
  slot.addEventListener("click", () => { if (slot.classList.contains("empty")) { pickFor = L; $("#file-input").click(); } });
  slot.addEventListener("dragover", (e) => { e.preventDefault(); slot.classList.add("drag"); });
  slot.addEventListener("dragleave", () => slot.classList.remove("drag"));
  slot.addEventListener("drop", (e) => {
    e.preventDefault();
    slot.classList.remove("drag");
    const files = [...e.dataTransfer.files].filter((f) => f.type.startsWith("audio") || /\.(mp3|wav|m4a|flac|aac|ogg|aiff?)$/i.test(f.name));
    if (!files.length) return toast("That doesn't look like a song file.");
    addSong(L, files[0]);
    if (files[1] && L === "A" && !state.songs.B) addSong("B", files[1]);
  });
});

/* ---------- planning ---------- */
async function makePlan(payload) {
  if (!state.songs.A) return toast("Add a song first ♪");
  show("#plan-block");
  $("#plan-area").innerHTML = `<div class="card fade-in"><div class="thinking">${pixels}<span class="shimmer">Thinking it through</span><span class="elapsed" id="think-t">0.0s</span></div></div>`;
  const t0 = performance.now();
  const timer = setInterval(() => { const el = $("#think-t"); if (el) el.textContent = ((performance.now() - t0) / 1000).toFixed(1) + "s"; }, 100);
  scrollTo("#plan-block");
  try {
    state.recipe = await api("/api/plan", { songs: songIds(), ...payload });
    renderPlan();
  } catch (e) {
    $("#plan-area").innerHTML = `<div class="card fade-in"><p class="error" style="margin:0">${esc(e.message)}</p></div>`;
  } finally {
    clearInterval(timer);
  }
}

function renderPlan() {
  const r = state.recipe;
  $("#plan-area").innerHTML = `<div class="card fade-in">
    <h2>${esc(r.title)}</h2><p class="summary">${esc(r.summary)}</p>
    <ol class="steps">${r.steps.map((s, i) => `<li><span class="n">${String(i + 1).padStart(2, "0")}</span><span>${esc(s.label)}</span></li>`).join("")}</ol>
    ${r.warnings.map((w) => `<div class="warn">${icon.warn}<span>${esc(w)}</span></div>`).join("")}
    <div class="actions"><button class="btn" id="plan-skip">Not quite</button><button class="btn primary" id="plan-go">${icon.spark}Make it</button></div>
  </div>`;
  $("#plan-skip").onclick = () => { show("#plan-block", false); $("#prompt").focus(); };
  $("#plan-go").onclick = runRecipe;
}

document.querySelectorAll("[data-preset]").forEach((b) => (b.onclick = () => makePlan({ preset: b.dataset.preset })));
$("#prompt-form").onsubmit = (e) => {
  e.preventDefault();
  const prompt = $("#prompt").value.trim();
  if (!prompt) return $("#prompt").focus();
  makePlan({ prompt, previous: state.followup ? state.lastRecipe : null });
  state.followup = false;
};
const ta = $("#prompt");
ta.addEventListener("input", () => { ta.style.height = "auto"; ta.style.height = ta.scrollHeight + "px"; });
ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#prompt-form").requestSubmit(); } });
let ph = 0;
setInterval(() => { if (document.activeElement !== ta && !ta.value) ta.placeholder = PLACEHOLDERS[(ph = (ph + 1) % PLACEHOLDERS.length)]; }, 4000);

/* ---------- running ---------- */
async function runRecipe() {
  const r = state.recipe;
  show("#result-block");
  $("#result-area").innerHTML = `<div class="card fade-in">
    <div class="thinking">${pixels}<span class="shimmer" id="run-msg">Mixing</span><span class="elapsed" id="run-t">0.0s</span></div>
    <div class="tasks" id="tasks"></div>
    <p class="summary" style="margin-top:12px;font-size:12.5px">Pulling vocals out takes a minute or two the first time for each song.</p></div>`;
  $("#plan-go").disabled = true;
  scrollTo("#result-block");
  let job;
  try {
    job = await api("/api/run", { recipe: r, songs: songIds() });
  } catch (e) {
    $("#result-area").innerHTML = `<div class="card"><p class="error" style="margin:0">${esc(e.message)}</p></div>`;
    return;
  }
  const t0 = performance.now();
  const tick = setInterval(() => { const el = $("#run-t"); if (el) el.textContent = ((performance.now() - t0) / 1000).toFixed(1) + "s"; }, 100);
  while (true) {
    renderTasks(r, job);
    if (job.state === "done" || job.state === "failed") break;
    await new Promise((res) => setTimeout(res, 700));
    job = await api(`/api/jobs/${job.id}`).catch(() => job);
  }
  clearInterval(tick);
  if (job.state === "done") renderResult(r, job);
  else {
    $("#run-msg").parentElement.outerHTML = `<p class="error" style="margin:0">Oops, that didn't work: ${esc(job.error)}</p>`;
  }
  const go = $("#plan-go");
  if (go) go.disabled = false;
}

function renderTasks(r, job) {
  const box = $("#tasks");
  if (!box) return;
  box.innerHTML = r.steps.map((s, i) => {
    const st = job.steps[i];
    const ic = st === "done" ? icon.check : st === "failed" ? icon.x : i + 1;
    const badge = st === "done" ? '<span class="badge ok">Completed</span>' : st === "running" ? '<span class="badge run">Working</span>' : st === "failed" ? '<span class="badge bad">Failed</span>' : "";
    return `<div class="task ${st}"><span class="ic">${ic}</span><span class="t">${esc(s.label)}</span>${badge}</div>`;
  }).join("");
}

function renderResult(r, job) {
  state.lastRecipe = r;
  const url = `/files/${encodeURIComponent(job.file)}`;
  $("#result-area").innerHTML = `<div class="card fade-in">
    <div class="result-top"><div class="disc" id="disc"></div>
      <div style="flex:1;min-width:0"><h2>${esc(r.title)}</h2><p class="summary">${fmt(job.duration)} · made in ${job.elapsed}s</p></div>
      <a class="btn primary" href="${url}?dl=1" download>${icon.down}Download</a></div>
    <audio controls src="${url}" id="player"></audio>
    <div class="followups"><div class="h">Tweak it</div>
      ${FOLLOWUPS.map((f) => `<button class="follow" data-f="${esc(f)}">${icon.ret}<span>${esc(f)}</span></button>`).join("")}</div>
  </div>`;
  const p = $("#player"), disc = $("#disc");
  p.onplay = () => disc.classList.add("playing");
  p.onpause = p.onended = () => disc.classList.remove("playing");
  document.querySelectorAll(".follow").forEach((b) => (b.onclick = () => {
    if (!state.ai) return toast("Tweaks need typed instructions, which are off.");
    ta.value = b.dataset.f;
    ta.dispatchEvent(new Event("input"));
    state.followup = true;
    $("#prompt-form").requestSubmit();
  }));
  ta.addEventListener("input", () => (state.followup = !!state.lastRecipe), { once: true });
}

/* ---------- boot ---------- */
renderSlots();
api("/api/status").then((s) => {
  state.ai = s.ai;
  if (!s.ai) { show("#ai-note"); ta.disabled = true; $(".send").disabled = true; ta.placeholder = "Typed instructions are off"; }
}).catch(() => {});
