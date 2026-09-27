/* The interface's own wiring: draw the reactor, keep the cards true, and show the work as it
   happens rather than after it. Everything arrives over one event stream from core/webui.py. */

const KEY = window.JARVIS_KEY;
const $ = (id) => document.getElementById(id);
const tasks = new Map();          /* id -> task, newest first in the DOM */
let busy = false;

/* ---- the reactor ------------------------------------------------------------------------ */
const canvas = $('reactor'), ctx = canvas.getContext('2d');
let spin = 0, level = 0, want = 0;

function fit() {
  const box = canvas.parentElement.getBoundingClientRect();
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.width = box.width * dpr; canvas.height = box.height * dpr;
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
}
addEventListener('resize', fit);

function ring(cx, cy, r, from, to, width, colour, glow) {
  ctx.beginPath();
  ctx.arc(cx, cy, r, from, to);
  ctx.lineWidth = width; ctx.strokeStyle = colour;
  ctx.shadowBlur = glow || 0; ctx.shadowColor = colour;
  ctx.stroke(); ctx.shadowBlur = 0;
}

function draw() {
  const w = canvas.clientWidth, h = canvas.clientHeight;
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) * 0.34;
  ctx.clearRect(0, 0, w, h);
  level += (want - level) * 0.12;
  spin += 0.004;

  /* the core: a hot centre under glass */
  const core = ctx.createRadialGradient(cx, cy, R * 0.02, cx, cy, R * 0.72);
  core.addColorStop(0, 'rgba(235,250,255,' + (0.85 + level * 0.15) + ')');
  core.addColorStop(0.18, 'rgba(120,215,255,0.55)');
  core.addColorStop(0.55, 'rgba(30,110,190,0.22)');
  core.addColorStop(1, 'rgba(8,18,36,0)');
  ctx.fillStyle = core;
  ctx.beginPath(); ctx.arc(cx, cy, R * 0.72, 0, Math.PI * 2); ctx.fill();

  /* the lattice: spokes and arcs, the reference's web in JARVIS's own geometry */
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(spin * 0.35);
  for (let i = 0; i < 12; i++) {
    const a = (i / 12) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * R * 0.78, Math.sin(a) * R * 0.78);
    ctx.lineTo(Math.cos(a) * R * 1.5, Math.sin(a) * R * 1.5);
    ctx.strokeStyle = 'rgba(120,190,255,0.16)'; ctx.lineWidth = 1; ctx.stroke();
  }
  for (let k = 1; k <= 4; k++) {
    ctx.beginPath();
    for (let i = 0; i <= 12; i++) {
      const a = (i / 12) * Math.PI * 2, r = R * (0.78 + k * 0.18);
      const x = Math.cos(a) * r, y = Math.sin(a) * r;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    }
    ctx.closePath();
    ctx.strokeStyle = 'rgba(120,190,255,' + (0.14 - k * 0.02) + ')';
    ctx.lineWidth = 1; ctx.stroke();
  }
  ctx.restore();

  /* two arcs facing each other across the core - warm on the left, cold on the right */
  const lean = Math.sin(spin * 1.6) * 0.05;
  ring(cx, cy, R * 1.12, 2.44 + lean, 3.84 + lean, 3.4, 'rgba(255,157,77,0.9)', 20);
  ring(cx, cy, R * 1.12, -0.70 - lean, 0.70 - lean, 3.4, 'rgba(73,184,255,0.95)', 20);

  /* the ticks around the rim */
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(-spin * 0.8);
  for (let i = 0; i < 72; i++) {
    const a = (i / 72) * Math.PI * 2, lit = i % 6 === 0;
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * R * 0.9, Math.sin(a) * R * 0.9);
    ctx.lineTo(Math.cos(a) * R * (lit ? 0.96 : 0.93), Math.sin(a) * R * (lit ? 0.96 : 0.93));
    ctx.strokeStyle = lit ? 'rgba(143,227,255,0.55)' : 'rgba(143,227,255,0.22)';
    ctx.lineWidth = lit ? 1.6 : 1; ctx.stroke();
  }
  ctx.restore();

  /* the voice, straight through the middle */
  ctx.beginPath();
  for (let x = -R * 0.66; x <= R * 0.66; x += 2) {
    const fade = 1 - Math.abs(x) / (R * 0.66);
    const y = Math.sin(x * 0.11 + spin * 9) * R * 0.2 * level * fade
            + Math.sin(x * 0.33 - spin * 13) * R * 0.08 * level * fade;
    x === -R * 0.66 ? ctx.moveTo(cx + x, cy + y) : ctx.lineTo(cx + x, cy + y);
  }
  ctx.strokeStyle = 'rgba(143,227,255,' + (0.35 + level * 0.6) + ')';
  ctx.lineWidth = 2; ctx.shadowBlur = 16; ctx.shadowColor = 'rgba(73,184,255,0.9)';
  ctx.stroke(); ctx.shadowBlur = 0;

  requestAnimationFrame(draw);
}

/* ---- drawing the cards ------------------------------------------------------------------ */
function facts(where, rows) {
  where.innerHTML = rows.map(r =>
    `<div class="fact"><div class="k">${esc(r.label)}</div><div class="v">${esc(r.value)}</div></div>`
  ).join('') || '<p class="quiet">Nothing to report.</p>';
}

function systems(rows) {
  const ready = rows.filter(r => r.ok).length;
  $('ready').textContent = ready + '/' + rows.length;
  $('systems').innerHTML = rows.map(r =>
    `<div class="line"><span class="name">${esc(r.name)}</span>
      <span class="state ${r.ok ? '' : 'off'}">${esc(r.state)}
      <span class="dot ${r.ok ? '' : 'off'}"></span></span></div>`).join('');
}

function taskCard(t) {
  const last = t.steps && t.steps.length ? t.steps[t.steps.length - 1].text : '';
  const age = t.state === 'running' ? 'WORKING' : (t.state === 'failed' ? 'FAILED' : 'DONE');
  return `<div class="task ${t.state}" data-id="${t.id}">
      <div class="what">${esc(t.text)}</div>
      <div class="how"><span class="pip"></span>${age}</div>
      ${last ? `<div class="last">${esc(last)}</div>` : ''}
    </div>`;
}

function paintTasks() {
  const all = [...tasks.values()].sort((a, b) => b.started - a.started);
  const live = all.filter(t => t.state === 'running').length;
  $('taskcount').textContent = live || all.length;
  $('tasks').innerHTML = all.length ? all.map(taskCard).join('')
                                    : '<p class="quiet">Nothing queued.</p>';
  [...document.querySelectorAll('.task')].forEach(el => {
    el.onclick = () => openTask(el.dataset.id);
  });
}

function openTask(id) {
  const t = tasks.get(id);
  if (!t) return;
  $('sheettitle').textContent = t.state === 'running' ? 'WORKING' : 'WHAT IT DID';
  $('sheetbody').innerHTML =
    `<div class="said">${esc(t.text)}</div>` +
    (t.answer ? `<div class="said" style="color:var(--cyan)">${esc(t.answer)}</div>` : '') +
    (t.steps || []).map(s =>
      `<div class="stepline"><span class="stage">${esc(s.stage || '')}</span>
       <span class="text">${esc(s.text)}</span></div>`).join('');
  $('sheet').classList.add('show');
  $('sheet').dataset.id = id;
}

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g,
    c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
}

function say(text) {
  const box = $('saying');
  box.textContent = text;
  box.classList.add('show');
  clearTimeout(say.timer);
  say.timer = setTimeout(() => box.classList.remove('show'), 9000);
}

/* ---- talking to the engine -------------------------------------------------------------- */
async function refresh() {
  try {
    const res = await fetch('/state?k=' + KEY, {cache: 'no-store'});
    if (!res.ok) return;
    const s = await res.json();
    systems(s.systems || []);
    facts($('awareness'), s.awareness || []);
    facts($('memory'), s.memory || []);
    (s.tasks || []).forEach(t => tasks.set(t.id, t));
    paintTasks();
    $('onlinedot').classList.remove('off');
    $('online').textContent = 'ONLINE';
  } catch (e) {
    $('onlinedot').classList.add('off');
    $('online').textContent = 'ENGINE AWAY';
  }
}

function listen() {
  const stream = new EventSource('/events?k=' + KEY);
  stream.onmessage = (e) => {
    let m; try { m = JSON.parse(e.data); } catch (err) { return; }
    if (m.kind === 'task') {
      tasks.set(m.task.id, m.task);
      paintTasks();
      if ($('sheet').classList.contains('show') && $('sheet').dataset.id === m.task.id) {
        openTask(m.task.id);
      }
      want = m.task.state === 'running' ? 0.85 : 0.12;
    } else if (m.kind === 'step') {
      const t = tasks.get(m.id);
      if (t) {
        (t.steps = t.steps || []).push({stage: m.stage, text: m.text});
        paintTasks();
        if ($('sheet').classList.contains('show') && $('sheet').dataset.id === m.id) openTask(m.id);
      }
      want = 0.9;
    } else if (m.kind === 'answer') {
      say(m.text);
      want = 0.2;
    } else if (m.kind === 'voice') {
      want = m.level != null ? m.level : (m.state === 'listening' ? 0.6 : 0.15);
    }
  };
  stream.onerror = () => { $('onlinedot').classList.add('off'); };
}

$('askform').addEventListener('submit', async (e) => {
  e.preventDefault();
  const text = $('ask').value.trim();
  if (!text || busy) return;
  $('ask').value = '';
  busy = true; want = 0.8;
  try {
    await fetch('/ask?k=' + KEY, {method: 'POST', headers: {'Content-Type': 'application/json'},
                                  body: JSON.stringify({text})});
  } catch (err) { say("I couldn't reach the engine, sir."); }
  busy = false;
});

/* the microphone: the browser's own ear, for typing-free asking */
$('mic').addEventListener('click', () => {
  const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!Rec) { say('This window has no speech recognition, sir - use the voice wake word.'); return; }
  const rec = new Rec();
  rec.lang = 'en-IN'; rec.interimResults = false;
  $('mic').classList.add('on'); want = 0.7;
  rec.onresult = (e) => { $('ask').value = e.results[0][0].transcript;
                          $('askform').dispatchEvent(new Event('submit')); };
  rec.onend = () => { $('mic').classList.remove('on'); want = 0.15; };
  try { rec.start(); } catch (err) { $('mic').classList.remove('on'); }
});

$('sheetclose').onclick = () => $('sheet').classList.remove('show');
$('sheet').onclick = (e) => { if (e.target === $('sheet')) $('sheet').classList.remove('show'); };
$('showtasks').onclick = () => {
  const running = [...tasks.values()].find(t => t.state === 'running') || [...tasks.values()][0];
  if (running) openTask(running.id); else say('Nothing to show, sir.');
};
$('showmemory').onclick = () => $('ask').focus();
$('showawareness').onclick = () => $('ask').focus();
$('showsystem').onclick = () => $('ask').focus();
addEventListener('keydown', (e) => {
  if (e.key === 'Escape') $('sheet').classList.remove('show');
  if (e.key === '/' && document.activeElement !== $('ask')) { e.preventDefault(); $('ask').focus(); }
});

fit(); draw(); refresh(); listen();
setInterval(refresh, 4000);
setInterval(() => { if (!busy) want = Math.max(0.1, want * 0.85); }, 1200);
$('ask').focus();
