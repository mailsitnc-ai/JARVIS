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
  const cx = w / 2, cy = h / 2, R = Math.min(w, h) * 0.42;
  ctx.clearRect(0, 0, w, h);
  level += (want - level) * 0.12;
  spin += 0.0035;
  const beat = 0.5 + 0.5 * Math.sin(spin * 7);          /* the reactor's idle pulse */
  const heat = 0.55 + level * 0.45 + beat * 0.06;

  /* --- the lattice it sits in, faint and far out ------------------------------------------ */
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(spin * 0.3);
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2;
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * R * 1.05, Math.sin(a) * R * 1.05);
    ctx.lineTo(Math.cos(a) * R * 1.75, Math.sin(a) * R * 1.75);
    ctx.strokeStyle = 'rgba(120,190,255,' + (i % 2 ? 0.05 : 0.11) + ')';
    ctx.lineWidth = 1; ctx.stroke();
  }
  for (let k = 1; k <= 3; k++) {
    ctx.beginPath(); ctx.arc(0, 0, R * (1.05 + k * 0.22), 0, Math.PI * 2);
    ctx.strokeStyle = 'rgba(120,190,255,' + (0.10 - k * 0.025) + ')';
    ctx.setLineDash(k === 2 ? [3, 9] : []); ctx.lineWidth = 1; ctx.stroke();
    ctx.setLineDash([]);
  }
  ctx.restore();

  /* --- the housing: ten copper coils around a dark rim ------------------------------------ */
  ring(cx, cy, R * 0.94, 0, Math.PI * 2, R * 0.16, 'rgba(10,22,42,0.92)', 0);
  ring(cx, cy, R * 0.94, 0, Math.PI * 2, 1.4, 'rgba(120,190,255,0.35)', 8);
  ring(cx, cy, R * 0.86, 0, Math.PI * 2, 1, 'rgba(120,190,255,0.22)', 0);
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(spin * 0.6);
  const COILS = 10;
  for (let i = 0; i < COILS; i++) {
    const a0 = (i / COILS) * Math.PI * 2 + 0.045;
    const a1 = ((i + 1) / COILS) * Math.PI * 2 - 0.045;
    const lit = 0.30 + 0.5 * Math.max(0, Math.sin(spin * 5 - i * 0.62));
    ring(0, 0, R * 0.94, a0, a1, R * 0.115, 'rgba(46,120,200,' + (0.22 + lit * 0.3) + ')', 0);
    ring(0, 0, R * 0.94, a0, a1, 2.2, 'rgba(143,227,255,' + (0.25 + lit * 0.55) + ')', 14);
    /* the winding inside each coil */
    for (let s = 1; s < 5; s++) {
      const a = a0 + (a1 - a0) * (s / 5);
      ctx.beginPath();
      ctx.moveTo(Math.cos(a) * R * 0.885, Math.sin(a) * R * 0.885);
      ctx.lineTo(Math.cos(a) * R * 1.0, Math.sin(a) * R * 1.0);
      ctx.strokeStyle = 'rgba(143,227,255,' + (0.10 + lit * 0.18) + ')';
      ctx.lineWidth = 1; ctx.stroke();
    }
  }
  ctx.restore();

  /* --- the tick ring, counter-rotating ---------------------------------------------------- */
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(-spin * 1.1);
  for (let i = 0; i < 96; i++) {
    const a = (i / 96) * Math.PI * 2, big = i % 8 === 0;
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * R * 0.78, Math.sin(a) * R * 0.78);
    ctx.lineTo(Math.cos(a) * R * (big ? 0.72 : 0.755), Math.sin(a) * R * (big ? 0.72 : 0.755));
    ctx.strokeStyle = big ? 'rgba(143,227,255,0.6)' : 'rgba(143,227,255,0.24)';
    ctx.lineWidth = big ? 1.7 : 1; ctx.stroke();
  }
  ctx.restore();

  /* --- the sweep: something is always being scanned --------------------------------------- */
  const sweep = ctx.createLinearGradient(cx - R, cy, cx + R, cy);
  sweep.addColorStop(0, 'rgba(73,184,255,0)');
  sweep.addColorStop(1, 'rgba(73,184,255,0.30)');
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(spin * 3.4);
  ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, R * 0.70, -0.42, 0); ctx.closePath();
  ctx.fillStyle = sweep; ctx.fill();
  ctx.restore();

  /* --- the element: a triangle inside a hexagon, the shape everyone knows ------------------ */
  ctx.save(); ctx.translate(cx, cy);
  ctx.rotate(-spin * 0.5);
  ctx.beginPath();
  for (let i = 0; i < 6; i++) {
    const a = (i / 6) * Math.PI * 2 - Math.PI / 2, r = R * 0.62;
    i ? ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r) : ctx.moveTo(Math.cos(a) * r, Math.sin(a) * r);
  }
  ctx.closePath();
  ctx.strokeStyle = 'rgba(143,227,255,0.45)'; ctx.lineWidth = 1.6;
  ctx.shadowBlur = 14; ctx.shadowColor = 'rgba(73,184,255,0.8)'; ctx.stroke(); ctx.shadowBlur = 0;

  ctx.rotate(spin * 1.6);
  ctx.beginPath();
  for (let i = 0; i < 3; i++) {
    const a = (i / 3) * Math.PI * 2 - Math.PI / 2, r = R * 0.5;
    i ? ctx.lineTo(Math.cos(a) * r, Math.sin(a) * r) : ctx.moveTo(Math.cos(a) * r, Math.sin(a) * r);
  }
  ctx.closePath();
  ctx.strokeStyle = 'rgba(190,240,255,' + (0.5 + heat * 0.3) + ')'; ctx.lineWidth = 2;
  ctx.shadowBlur = 20; ctx.shadowColor = 'rgba(143,227,255,0.9)'; ctx.stroke(); ctx.shadowBlur = 0;
  ctx.restore();

  /* --- spokes feeding the core ------------------------------------------------------------ */
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(spin * 0.9);
  for (let i = 0; i < 18; i++) {
    const a = (i / 18) * Math.PI * 2;
    const pull = 0.5 + 0.5 * Math.sin(spin * 6 - i * 0.5);
    ctx.beginPath();
    ctx.moveTo(Math.cos(a) * R * 0.30, Math.sin(a) * R * 0.30);
    ctx.lineTo(Math.cos(a) * R * 0.70, Math.sin(a) * R * 0.70);
    ctx.strokeStyle = 'rgba(143,227,255,' + (0.07 + pull * 0.20 * heat) + ')';
    ctx.lineWidth = 1.3; ctx.stroke();
  }
  ctx.restore();

  /* --- the core: white hot, blooming ------------------------------------------------------ */
  const bloom = ctx.createRadialGradient(cx, cy, 0, cx, cy, R * 1.05);
  bloom.addColorStop(0, 'rgba(255,255,255,' + Math.min(1, 1.05 * heat) + ')');
  bloom.addColorStop(0.09, 'rgba(226,250,255,' + (0.96 * heat) + ')');
  bloom.addColorStop(0.20, 'rgba(140,225,255,' + (0.70 * heat) + ')');
  bloom.addColorStop(0.38, 'rgba(60,160,235,' + (0.34 * heat) + ')');
  bloom.addColorStop(0.70, 'rgba(26,86,160,' + (0.14 * heat) + ')');
  bloom.addColorStop(1, 'rgba(8,18,36,0)');
  ctx.fillStyle = bloom;
  ctx.beginPath(); ctx.arc(cx, cy, R * 1.05, 0, Math.PI * 2); ctx.fill();

  /* the inner coil ring, six small windings tight around the core */
  ctx.save(); ctx.translate(cx, cy); ctx.rotate(-spin * 2.2);
  for (let i = 0; i < 6; i++) {
    const a0 = (i / 6) * Math.PI * 2 + 0.10, a1 = ((i + 1) / 6) * Math.PI * 2 - 0.10;
    const lit = 0.4 + 0.6 * Math.max(0, Math.sin(spin * 8 - i * 1.05));
    ring(0, 0, R * 0.40, a0, a1, R * 0.05, 'rgba(120,215,255,' + (0.14 + lit * 0.22) + ')', 0);
    ring(0, 0, R * 0.40, a0, a1, 1.6, 'rgba(226,250,255,' + (0.3 + lit * 0.5) + ')', 12);
  }
  ctx.restore();

  /* the hot eye itself */
  ring(cx, cy, R * 0.30, 0, Math.PI * 2, 2.4, 'rgba(245,253,255,' + (0.55 + heat * 0.45) + ')', 30);
  ring(cx, cy, R * 0.21, 0, Math.PI * 2, 1.4, 'rgba(180,240,255,0.7)', 18);
  ctx.beginPath(); ctx.arc(cx, cy, R * 0.115 * (0.94 + beat * 0.10), 0, Math.PI * 2);
  ctx.fillStyle = 'rgba(255,255,255,' + (0.80 + heat * 0.2) + ')';
  ctx.shadowBlur = 44; ctx.shadowColor = 'rgba(150,225,255,1)'; ctx.fill(); ctx.shadowBlur = 0;

  /* --- your voice, straight through the middle -------------------------------------------- */
  ctx.beginPath();
  for (let x = -R * 0.58; x <= R * 0.58; x += 2) {
    const fade = 1 - Math.abs(x) / (R * 0.58);
    const y = Math.sin(x * 0.11 + spin * 9) * R * 0.17 * level * fade
            + Math.sin(x * 0.33 - spin * 13) * R * 0.07 * level * fade;
    x === -R * 0.58 ? ctx.moveTo(cx + x, cy + y) : ctx.lineTo(cx + x, cy + y);
  }
  ctx.strokeStyle = 'rgba(235,250,255,' + (0.3 + level * 0.65) + ')';
  ctx.lineWidth = 2; ctx.shadowBlur = 18; ctx.shadowColor = 'rgba(73,184,255,0.95)';
  ctx.stroke(); ctx.shadowBlur = 0;

  /* --- the two arcs facing each other across it ------------------------------------------- */
  const lean = Math.sin(spin * 1.6) * 0.05;
  ring(cx, cy, R * 1.20, 2.44 + lean, 3.84 + lean, 3.4, 'rgba(255,157,77,0.9)', 22);
  ring(cx, cy, R * 1.20, -0.70 - lean, 0.70 - lean, 3.4, 'rgba(73,184,255,0.95)', 22);

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
    } else if (m.kind === 'confirm') {
      $('asking').textContent = m.summary || 'JARVIS wants to do something.';
      $('askingdetail').textContent = m.details || '';
      $('permit').dataset.id = m.id;
      $('permit').classList.add('show');
      want = 0.5;
    } else if (m.kind === 'confirmed') {
      if ($('permit').dataset.id === m.id) $('permit').classList.remove('show');
    } else if (m.kind === 'reset') {
      tasks.clear(); paintTasks();
      $('saying').classList.remove('show');
      say('Clean slate, sir.');
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

function decide(choice) {
  const id = $('permit').dataset.id;
  $('permit').classList.remove('show');
  fetch('/decide?k=' + KEY, {method: 'POST', headers: {'Content-Type': 'application/json'},
                             body: JSON.stringify({id, decision: choice})}).catch(() => {});
}
$('once').onclick = () => decide('once');
$('always').onclick = () => decide('always');
$('deny').onclick = () => decide('deny');

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
