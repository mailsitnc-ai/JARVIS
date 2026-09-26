"""Talk to JARVIS from your phone: hold a button, speak, hear the answer.

The second video calls the assistant on the phone. A real phone number means telephony (a Twilio
number, a public server, per-minute charges); this does the same thing over your own network: the
Mac serves one small page, your phone opens it, you hold the button and talk, and JARVIS answers in
its own voice. Your speech is transcribed by the Whisper on this machine - the audio never leaves it.

A phone will only hand a web page its microphone over HTTPS, so the page is served with a
certificate JARVIS makes for itself. Your phone shows a "not private" warning the first time
(nobody signed the certificate - it's your own Mac on your own network); accept it once and the
microphone works from then on.

Away from home, `expose()` publishes just this page through a tunnel - one outbound connection
from the Mac, no VPN, nothing about your networking changed - and that address has a real
certificate, so there's no warning at all.

The page is locked to a token that's generated once and kept in JARVIS's data folder, so only a
device you've given the link to can talk to it.
"""
from __future__ import annotations

import json
import re
import secrets
import socket
import ssl
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PORT = 8765
MAX_AUDIO = 8 * 1024 * 1024        # a held button, not a podcast


def lan_ip() -> str:
    """This Mac's address on the home network - what you type into the phone."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.connect(("192.0.2.1", 1))       # a reserved address: nothing is actually sent
        return probe.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        probe.close()


def certificate(ip: str | None = None):
    """A certificate for this Mac, made once with openssl and kept in JARVIS's folder. Remade if
    the machine's address on the network has changed, so the phone keeps trusting it."""
    from core import oslayer

    folder = Path(oslayer.user_data_dir())
    cert, key = folder / "talk-cert.pem", folder / "talk-key.pem"
    where = ip or lan_ip()
    if cert.exists() and key.exists():
        try:
            names = subprocess.run(["openssl", "x509", "-in", str(cert), "-noout", "-text"],
                                   capture_output=True, text=True, timeout=10).stdout
            if where in names:
                return cert, key
        except (OSError, subprocess.SubprocessError):
            return cert, key
    try:
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                        "-keyout", str(key), "-out", str(cert), "-days", "3650",
                        "-subj", "/CN=JARVIS", "-addext",
                        f"subjectAltName=IP:{where},IP:127.0.0.1,DNS:localhost"],
                       capture_output=True, timeout=60, check=True)
        key.chmod(0o600)
    except (OSError, subprocess.SubprocessError):
        return None, None
    return cert, key

WORKER = """/* Two jobs: skip ngrok's free-tier warning page, and keep a copy of JARVIS on the
   phone - so the icon opens the real thing even when the Mac is asleep, off, or off the internet.
   ngrok answers a 404 error page when the Mac is away, which is a perfectly good HTTP response, so
   anything that isn't ok counts as "not there" and the kept copy is served instead. */
const SHELL = 'jarvis-shell-1', KEPT = 'jarvis-page';

function asked(req) {
  return new Request(req, {
    headers: new Headers([...req.headers.entries(), ['ngrok-skip-browser-warning', 'jarvis']]),
    mode: req.mode === 'navigate' ? 'same-origin' : req.mode,
    redirect: 'follow'
  });
}

self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', event => {
  const req = event.request;
  if (req.method !== 'GET' || !req.url.startsWith(self.location.origin)) return;
  if (req.mode === 'navigate') {
    event.respondWith((async () => {
      try {
        const fresh = await fetch(asked(req));
        if (!fresh.ok) throw new Error('not there');
        (await caches.open(SHELL)).put(KEPT, fresh.clone());
        return fresh;
      } catch (e) {
        return (await caches.match(KEPT, {cacheName: SHELL})) || Response.error();
      }
    })());
    return;
  }
  event.respondWith(fetch(asked(req)).catch(() => fetch(req)));
});
"""

PAGE = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>JARVIS</title>
<!-- Add to Home Screen: the icon opens straight into a call, like ringing someone. -->
<link rel="manifest" id="mf">
<link rel="apple-touch-icon" id="ic">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="JARVIS">
<meta name="theme-color" content="#07090d">
<style>
  :root { color-scheme: dark; }
  body { margin:0; min-height:100vh; background:#07090d; color:#e8edf2; display:flex;
         flex-direction:column; font:16px/1.5 -apple-system,system-ui,sans-serif; }
  header { padding:20px 16px 4px; letter-spacing:.34em; font-size:13px; color:#7fd4ff;
           text-align:center; }
  .state { text-align:center; color:#7c8899; font-size:14px; padding:2px 16px 8px; min-height:20px; }
  #log { flex:1; width:100%; max-width:640px; margin:0 auto; padding:8px 16px 190px;
         box-sizing:border-box; overflow-y:auto; }
  .turn { margin:10px 0; padding:11px 14px; border-radius:16px; max-width:85%; white-space:pre-wrap; }
  .me { margin-left:auto; background:#16324a; border-bottom-right-radius:4px; }
  .jarvis { background:#141922; border:1px solid #23303f; border-bottom-left-radius:4px; }
  footer { position:fixed; bottom:0; left:0; right:0; padding:16px 0 30px; display:flex;
           flex-direction:column; align-items:center; gap:14px;
           background:linear-gradient(transparent,#07090d 30%); }
  #talk { width:126px; height:126px; border-radius:50%; border:none; color:#04121c; font-size:17px;
          font-weight:600; background:radial-gradient(circle at 50% 35%, #8fe3ff, #29a8dd);
          box-shadow:0 0 40px rgba(41,168,221,.45); touch-action:none; user-select:none; }
  #talk.on { background:radial-gradient(circle at 50% 35%, #ffd39a, #f0803a);
             box-shadow:0 0 60px rgba(240,128,58,.6); transform:scale(1.06); }
  #talk:disabled { opacity:.45; box-shadow:none; }
  #call { border:1px solid #2b6b8d; background:#0d1a24; color:#8fe3ff; border-radius:24px;
          padding:11px 26px; font-size:15px; font-weight:600; }
  #call.on { border-color:#8b2f2f; background:#2a1113; color:#ff9c9c; }
  #connect { position:fixed; inset:0; z-index:9; display:none; flex-direction:column; gap:22px;
             align-items:center; justify-content:center; background:#07090d; }
  #connect.show { display:flex; }
  #connect .ring { width:168px; height:168px; border-radius:50%;
                   background:radial-gradient(circle at 50% 35%, #8fe3ff, #29a8dd);
                   box-shadow:0 0 70px rgba(41,168,221,.5); animation:pulse 1.8s ease-in-out infinite; }
  #connect p { color:#7c8899; }
  @keyframes pulse { 50% { transform:scale(1.06); box-shadow:0 0 90px rgba(41,168,221,.75); } }
  #setup { position:fixed; inset:0; z-index:10; display:none; flex-direction:column; gap:16px;
           align-items:center; justify-content:center; padding:28px; background:#07090dfa; }
  #setup p { color:#9aa7b8; max-width:22em; text-align:center; line-height:1.5; }
  #setup input { width:100%; max-width:22em; padding:13px 15px; border-radius:12px;
                 border:1px solid #23303f; background:#0d1218; color:#e8f1fb; font-size:16px; }
  #setup button { border-radius:12px; border:1px solid #29506b; background:#10202c; color:#8fe3ff;
                  padding:11px 20px; font-size:15px; font-weight:600; }
  #kx { position:fixed; top:10px; right:12px; z-index:8; background:none; border:none;
        color:#4d5a6b; font-size:12px; }
</style></head>
<body>
  <header>J A R V I S</header>
  <div class="state" id="state">hold to talk, or press Call to keep the line open</div>
  <div id="log"></div>
  <footer>
    <button id="talk">hold<br>to talk</button>
    <button id="call">Call</button>
  </footer>
  <div id="connect"><div class="ring"></div><p id="connectnote">tap anywhere to connect</p></div>
  <div id="setup">
    <p>Paste a Groq or Gemini key. It stays on this phone and lets me answer you here when your
       Mac is asleep or off.</p>
    <input id="kf" type="password" autocomplete="off" spellcheck="false" placeholder="gsk_... or AIza...">
    <button id="ks">Keep it on this phone</button>
  </div>
  <button id="kx" title="answer on this phone when the Mac is away">on-phone mode</button>
<script>
if ('serviceWorker' in navigator) {        // see WORKER above: skips ngrok's warning page
  navigator.serviceWorker.register('/sw.js' + location.search).catch(() => {});
}
const params = new URLSearchParams(location.search);
const token = params.get('k') || '';
document.getElementById('mf').href = '/manifest.webmanifest?k=' + encodeURIComponent(token);
document.getElementById('ic').href = '/icon-192.png?k=' + encodeURIComponent(token);
const log = document.getElementById('log'), state = document.getElementById('state');
const talk = document.getElementById('talk'), call = document.getElementById('call');
let stream = null, recorder = null, onCall = false, busy = false, wakeLock = null;

function say(who, text) {
  const div = document.createElement('div');
  div.className = 'turn ' + who;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

async function mic() {
  if (!stream) stream = await navigator.mediaDevices.getUserMedia({audio: true});
  return stream;
}

/* Record until you stop speaking (for a call), or until the button is let go. */
function record(untilSilence) {
  return new Promise(async (resolve) => {
    let src;
    try { src = await mic(); } catch (e) {
      state.textContent = 'the browser blocked the microphone (the page must be https)';
      return resolve(null);
    }
    const rec = new MediaRecorder(src);
    const chunks = [];
    rec.ondataavailable = e => chunks.push(e.data);
    rec.onstop = () => resolve(new Blob(chunks));
    rec.start();
    recorder = rec;
    talk.classList.add('on');
    state.textContent = 'listening...';
    if (!untilSilence) return;
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 2048;
    ctx.createMediaStreamSource(src).connect(analyser);
    const buf = new Float32Array(analyser.fftSize);
    let spoke = false, quietAt = performance.now(), began = performance.now();
    const watch = setInterval(() => {
      if (rec.state !== 'recording') { clearInterval(watch); ctx.close(); return; }
      analyser.getFloatTimeDomainData(buf);
      let sum = 0;
      for (let i = 0; i < buf.length; i++) sum += buf[i] * buf[i];
      const level = Math.sqrt(sum / buf.length), now = performance.now();
      if (level > 0.02) { spoke = true; quietAt = now; }
      const nothingSaid = !spoke && now - began > 9000;
      const finished = spoke && now - quietAt > 1200;
      if (nothingSaid || finished || now - began > 25000) {
        clearInterval(watch); ctx.close();
        stop();
      }
    }, 100);
  });
}

function stop() {
  if (!recorder) return;
  talk.classList.remove('on');
  const rec = recorder;
  recorder = null;
  if (rec.state === 'recording') rec.stop();
}

function hush() {          /* let go of the button: stop whichever ear is open */
  if (listening) { try { listening.stop(); } catch (e) {} }
  else stop();
}

async function ask(blob) {
  if (!blob) { state.textContent = 'the microphone gave nothing back'; return null; }
  if (blob.size < 2000) { state.textContent = "didn't catch that - say a bit more"; return null; }
  state.textContent = 'thinking (' + Math.round(blob.size / 1024) + ' KB sent)...';
  /* Never wait forever: if the Mac doesn't answer, say so and let the next turn happen. */
  const bail = new AbortController();
  const timer = setTimeout(() => bail.abort(), 45000);
  try {
    const res = await fetch('/ask?k=' + encodeURIComponent(token),
                            {method: 'POST', body: blob, signal: bail.signal});
    if (!res.ok) { say('jarvis', 'the Mac said no (' + res.status + ')'); return null; }
    return await res.json();
  } catch (e) {
    say('jarvis', e.name === 'AbortError' ? 'no answer from the Mac after 45 seconds'
                                          : "can't reach your Mac - it's asleep or switched off");
    return null;
  } finally {
    clearTimeout(timer);
  }
}

function play(url) {
  return new Promise((resolve) => {
    const audio = new Audio(url + '?k=' + encodeURIComponent(token));
    audio.onended = audio.onerror = resolve;
    audio.play().catch(resolve);
  });
}

async function turn(untilSilence) {
  busy = true;
  talk.disabled = true;
  if (offline) {
    try { await phoneTurn(); } catch (e) { state.textContent = 'that went wrong: ' + e.message; }
    busy = false;
    talk.disabled = false;
    return;
  }
  try {
    const blob = await record(untilSilence);
    const data = await ask(blob);
    if (data) {
      if (data.heard) say('me', data.heard);
      say('jarvis', data.text || data.error || 'no answer');
      state.textContent = onCall ? 'speaking...' : 'hold to talk, or press Call';
      if (data.audio) await play(data.audio);        // wait, so it doesn't hear itself
    }
  } catch (e) {
    state.textContent = "lost your Mac - asleep, off, or off the internet";
    onCall = false;
    call.classList.remove('on');
    call.textContent = 'Call';
  }
  busy = false;
  talk.disabled = false;
  state.textContent = onCall ? 'listening...' : 'hold to talk, or press Call';
}

/* ---- JARVIS on the phone itself ----------------------------------------------------------
   When the Mac is asleep or switched off there is nothing to call, so the phone answers instead:
   its own speech recognition, the same model JARVIS uses on the Mac (your key, kept on this phone
   and sent nowhere else), and the phone's own voice. Anything that actually needs the Mac is put in
   a queue and handed over the moment it's back. */
let offline = false, listening = null, brain = null;
try { brain = JSON.parse(localStorage.getItem('jarvis-brain') || 'null'); } catch (e) {}

const RULES = "You are JARVIS, speaking to Shivam, who you address as sir. You are running on his "
  + "phone because his Mac is asleep or switched off, so you cannot touch the Mac right now. "
  + "Answer in at most three short spoken sentences - no lists, no markdown. If what he asks needs "
  + "his Mac (opening apps or files, WhatsApp, screenshots, typing, anything on the laptop), reply "
  + "with exactly LAPTOP: followed by his request in plain words and nothing else.";

/* The key stays yours: you paste it into this phone once, it lives in this phone's storage, and it
   goes straight to the model from here. The Mac never hands it over and never sees it again. */
const MODELS = {groq: '__GROQ_MODEL__', gemini: '__GEMINI_MODEL__'};

function setUpBrain(pasted) {
  const key = (pasted || '').trim();
  const which = key.startsWith('gsk_') ? 'groq' : (key.startsWith('AIza') ? 'gemini' : '');
  if (!which) return false;
  brain = {}; brain[which] = {key: key, model: MODELS[which]};
  try { localStorage.setItem('jarvis-brain', JSON.stringify(brain)); } catch (e) {}
  return which;
}

function showSetup() {
  const panel = document.getElementById('setup');
  panel.style.display = 'flex';
  document.getElementById('kf').focus();
}

document.getElementById('ks').addEventListener('click', () => {
  const which = setUpBrain(document.getElementById('kf').value);
  document.getElementById('kf').value = '';
  document.getElementById('setup').style.display = 'none';
  say('jarvis', which ? 'Kept on this phone, sir. I can answer here even with the Mac off.'
                      : "That doesn't look like a Groq or Gemini key, sir.");
});
document.getElementById('kx').addEventListener('click', () => showSetup());

function heardOnPhone() {
  return new Promise((resolve) => {
    const Rec = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!Rec) { resolve(''); return; }
    const rec = new Rec();
    rec.lang = 'en-IN'; rec.interimResults = false; rec.maxAlternatives = 1;
    let said = '';
    rec.onresult = e => { said = e.results[0][0].transcript; };
    rec.onerror = () => {};
    rec.onend = () => { listening = null; resolve(said.trim()); };
    listening = rec;
    try { rec.start(); } catch (e) { listening = null; resolve(''); }
  });
}

async function askModel(url, headers, body, dig) {
  const res = await fetch(url, {method: 'POST', headers: headers, body: JSON.stringify(body)});
  if (!res.ok) return '';
  return (dig(await res.json()) || '').trim();
}

async function think(text) {
  if (!brain) return "I can't reach your Mac, sir, and I've nothing to think with from here yet - "
                   + "open this once while the Mac is awake and I'll keep what I need.";
  const tries = [];
  if (brain.groq) tries.push(() => askModel(
    'https://api.groq.com/openai/v1/chat/completions',
    {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + brain.groq.key},
    {model: brain.groq.model, max_tokens: 300,
     messages: [{role: 'system', content: RULES}, {role: 'user', content: text}]},
    d => d.choices && d.choices[0] && d.choices[0].message.content));
  if (brain.gemini) tries.push(() => askModel(
    'https://generativelanguage.googleapis.com/v1beta/models/' + brain.gemini.model
      + ':generateContent?key=' + encodeURIComponent(brain.gemini.key),
    {'Content-Type': 'application/json'},
    {system_instruction: {parts: [{text: RULES}]},
     contents: [{role: 'user', parts: [{text: text}]}]},
    d => d.candidates && d.candidates[0] && d.candidates[0].content.parts[0].text));
  for (const attempt of tries) {
    try { const answer = await attempt(); if (answer) return answer; } catch (e) {}
  }
  return "I couldn't reach anything to think with, sir - your phone may be offline too.";
}

function speak(text) {
  return new Promise((resolve) => {
    try {
      const line = new SpeechSynthesisUtterance(text);
      line.onend = line.onerror = resolve;
      speechSynthesis.speak(line);
    } catch (e) { resolve(); }
  });
}

function waiting() { try { return JSON.parse(localStorage.getItem('jarvis-queue') || '[]'); } catch (e) { return []; } }
function keep(job) { localStorage.setItem('jarvis-queue', JSON.stringify(waiting().concat([job]))); }

async function handOver() {        /* the Mac is back: give it what was asked while it was away */
  const jobs = waiting();
  if (!jobs.length) return;
  localStorage.setItem('jarvis-queue', '[]');
  for (const job of jobs) {
    try {
      const res = await fetch('/say?k=' + encodeURIComponent(token), {method: 'POST', body: job});
      const data = await res.json();
      say('jarvis', (data.text || 'Done, sir.') + '   \u2014 ' + job);
    } catch (e) { keep(job); }
  }
}

async function phoneTurn() {
  state.textContent = 'listening (on your phone)...';
  const text = await heardOnPhone();
  if (!text) { state.textContent = "didn't catch that - your Mac is away, I'm listening here"; return; }
  say('me', text);
  state.textContent = 'thinking (on your phone)...';
  let reply = await think(text);
  if (/^LAPTOP:/i.test(reply)) {
    keep(reply.replace(/^LAPTOP:[ ]*/i, '').trim());
    reply = "Your Mac is away, sir. I've noted that and I'll do it the moment it's back.";
  }
  say('jarvis', reply);
  state.textContent = "your Mac is away - I'm answering from your phone";
  await speak(reply);
}

/* A call: listen, answer, listen again, until you hang up. */
async function conversation() {
  while (onCall) {
    await turn(true);
    if (!onCall) break;
    await new Promise(r => setTimeout(r, 250));
  }
}

call.addEventListener('click', async () => {
  if (call.dataset.busy === '1') return;      // a second tap while it's starting used to re-announce
  call.dataset.busy = '1';
  setTimeout(() => { call.dataset.busy = '0'; }, 600);
  onCall = !onCall;
  call.classList.toggle('on', onCall);
  call.textContent = onCall ? 'End call' : 'Call';
  if (onCall) {
    try { wakeLock = await navigator.wakeLock.request('screen'); } catch (e) {}
    say('jarvis', 'Line open, sir. Just talk.');
    conversation();
  } else {
    stop();
    if (wakeLock) { try { wakeLock.release(); } catch (e) {} wakeLock = null; }
    state.textContent = 'hold to talk, or press Call';
  }
});

/* Opened from the home-screen icon (or any ?call=1 link): one tap and you're connected. A phone
   won't give out the microphone without a tap, so that's the fewest taps possible. */
if (params.get('call') === '1') {
  const connect = document.getElementById('connect');
  connect.classList.add('show');
  connect.addEventListener('click', () => {
    connect.classList.remove('show');
    call.click();
  }, {once: true});
}

/* Say whether the Mac is even there before you start talking to it: a failed call used to look
   like a broken page, when the answer was simply that the Mac was asleep. Retries quietly, so the
   moment it wakes up the page says so without being reloaded. */
async function alive() {
  const note = document.getElementById('connectnote');
  for (let tries = 0; ; tries++) {
    let up = false;
    try {
      const res = await fetch('/alive?k=' + encodeURIComponent(token), {cache: 'no-store'});
      up = res.ok;
    } catch (e) { up = false; }
    if (up) {
      offline = false;
      if (note) note.textContent = 'tap anywhere to connect';
      if (tries) state.textContent = 'your Mac is back, sir';
      await handOver();        /* hand over anything asked while the Mac was away */
      return;
    }
    offline = true;
    if (note) note.textContent = brain ? 'your Mac is away - tap to talk to me here'
                                       : "your Mac isn't reachable - waiting for it";
    state.textContent = brain ? "your Mac is away - I'm answering from your phone"
                              : "can't reach your Mac - it's asleep or switched off";
    await new Promise(r => setTimeout(r, 5000));
  }
}
alive();

talk.addEventListener('pointerdown', e => { e.preventDefault(); if (!onCall && !busy) turn(false); });
talk.addEventListener('pointerup', e => { e.preventDefault(); if (!onCall) hush(); });
talk.addEventListener('pointercancel', () => { if (!onCall) hush(); });
talk.addEventListener('pointerleave', () => { if (!onCall) hush(); });
</script></body></html>
"""


# ---- reaching it from outside the house (a tunnel, not a VPN) -----------------------------------
# A tunnel is one outbound connection from this Mac that publishes THIS PAGE and nothing else:
# nothing about the Mac's networking changes, and none of your other traffic goes through it.
# Cloudflare hands back an https address with a real certificate, so the phone stops warning about
# the Mac's self-signed one - and it works on mobile data, not just at home.

# Two ways out to the internet. Cloudflare needs nothing at all but hands out a different address
# every time; ngrok's free account includes ONE address that never changes, which is what a
# home-screen icon needs - so if a fixed address has been set up, that's what we use.
NGROK = Path.home() / "bin" / "ngrok"
CLOUDFLARED = Path.home() / "bin" / "cloudflared"
DOWNLOAD = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
            "cloudflared-darwin-{arch}.tgz")
_TUNNEL: dict = {"proc": None, "url": "", "log": None}
_LOCAL = threading.local()


def on_a_call() -> bool:
    """Is this thread answering someone on the phone? They can't click a button on the Mac, so a
    request that needs approval must be refused straight away instead of leaving them on hold."""
    return bool(getattr(_LOCAL, "calling", False))


def _tunnel_file() -> Path:
    from core import oslayer
    return Path(oslayer.user_data_dir()) / "talk_tunnel.json"


def _remember_tunnel(url: str, pid: int) -> None:
    """Keep the address on disk as well as in memory, so 'where's the talk page' can still answer
    it later - and so a tunnel left running can be found and closed."""
    try:
        _tunnel_file().write_text(json.dumps({"url": url, "pid": int(pid)}))
    except OSError:
        pass


def _remembered() -> tuple[str, int]:
    try:
        saved = json.loads(_tunnel_file().read_text())
        return str(saved.get("url") or ""), int(saved.get("pid") or 0)
    except (OSError, ValueError, TypeError):
        return "", 0


def _alive(pid: int) -> bool:
    import os
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def tunnel_cli() -> str | None:
    """Where the tunnel program is, if it's here."""
    import shutil

    if CLOUDFLARED.exists():
        return str(CLOUDFLARED)
    return shutil.which("cloudflared")


def install_tunnel() -> str | None:
    """Fetch the tunnel program (one file, into ~/bin - no admin rights, nothing system-wide)."""
    import platform
    import tarfile
    import tempfile
    import urllib.request

    arch = "arm64" if platform.machine() in ("arm64", "aarch64") else "amd64"
    CLOUDFLARED.parent.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp) / "cloudflared.tgz"
            urllib.request.urlretrieve(DOWNLOAD.format(arch=arch), bundle)
            with tarfile.open(bundle) as archive:
                archive.extractall(tmp)
            Path(tmp, "cloudflared").replace(CLOUDFLARED)
        CLOUDFLARED.chmod(0o755)
    except Exception as exc:
        return f"I couldn't fetch the tunnel program: {exc}"
    return None


def fixed_address() -> str:
    """The address that never changes, if one has been set up (an ngrok free static domain)."""
    try:
        from core.config import load_settings
        return str(load_settings().get("talk.address", "") or "").strip()
    except Exception:
        return ""


def _ngrok_ready() -> bool:
    """Is ngrok here and signed in? (The token lives in ngrok's own config, never in JARVIS.)"""
    if not NGROK.exists():
        return False
    try:
        done = subprocess.run([str(NGROK), "config", "check"], capture_output=True, text=True,
                              timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return done.returncode == 0


def expose(port: int = PORT, secret: str | None = None, wait: float = 40.0) -> str:
    """Publish the talk page on the internet so you can call JARVIS when you're out."""
    import tempfile

    if public_url():
        return f"Already reachable from outside: {public_url()}"
    address = fixed_address()
    if address and _ngrok_ready():
        return _expose_fixed(address, port, secret, wait)
    cli = tunnel_cli()
    if not cli:
        problem = install_tunnel()
        if problem:
            return problem
        cli = tunnel_cli()
    log = Path(tempfile.gettempdir()) / "jarvis-tunnel.log"
    try:
        handle = open(log, "w")
        proc = subprocess.Popen([cli, "tunnel", "--no-autoupdate", "--no-tls-verify",
                                 "--url", f"https://127.0.0.1:{int(port)}"],
                                stdout=handle, stderr=subprocess.STDOUT, close_fds=True)
    except OSError as exc:
        return f"I couldn't start the tunnel: {exc}"
    _TUNNEL.update(proc=proc, log=handle, url="")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(1.0)
        if proc.poll() is not None:
            return "The tunnel stopped before it was ready - check your internet connection."
        found = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", log.read_text(errors="ignore"))
        if found:
            _TUNNEL["url"] = f"{found.group(0)}/?k={secret or token()}&call=1"
            _remember_tunnel(_TUNNEL["url"], proc.pid)
            return (f"You can call me from anywhere now: {_TUNNEL['url']}\n"
                    "Real certificate, so no warning - add it to your home screen. Anyone with that "
                    "exact link could talk to me, so keep it to yourself; say 'stop sharing the talk "
                    "page' when you want it closed. The address changes each time it's opened.")
    unexpose()
    return "The tunnel didn't come up in time - try again in a moment."


def _expose_fixed(address: str, port: int, secret: str | None, wait: float) -> str:
    """Publish at YOUR address, the one that doesn't change - so the icon on your phone keeps
    working for good."""
    import tempfile

    log = Path(tempfile.gettempdir()) / "jarvis-tunnel.log"
    try:
        handle = open(log, "w")
        proc = subprocess.Popen([str(NGROK), "http", f"https://127.0.0.1:{int(port)}",
                                 "--domain", address, "--host-header", "rewrite",
                                 "--log", "stdout", "--log-format", "logfmt"],
                                stdout=handle, stderr=subprocess.STDOUT, close_fds=True)
    except OSError as exc:
        return f"I couldn't start the tunnel: {exc}"
    _TUNNEL.update(proc=proc, log=handle, url="")
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        time.sleep(1.0)
        text = log.read_text(errors="ignore")
        if proc.poll() is not None:
            detail = [line for line in text.splitlines() if "err" in line.lower()]
            return ("The tunnel stopped: " + (detail[-1][:200] if detail else "no reason given"))
        if "started tunnel" in text or f"url=https://{address}" in text:
            _TUNNEL["url"] = f"https://{address}/?k={secret or token()}&call=1"
            _remember_tunnel(_TUNNEL["url"], proc.pid)
            return (f"You can call me from anywhere: {_TUNNEL['url']}\n"
                    "That address is yours and doesn't change, so the icon on your phone will keep "
                    "working. Keep the link to yourself - anyone with it could talk to me.")
    unexpose()
    return "The tunnel didn't come up in time - try again in a moment."


def tunnel_healthy(timeout: float = 4.0) -> bool:
    """Is the outside address really connected? A tunnel process can live through the Mac sleeping
    while its connection is long gone, and then the icon on your phone opens nothing. ngrok's own
    agent (on 127.0.0.1:4040, no internet needed) is the honest answer; if it can't be asked, the
    process still being alive is the best we have."""
    import urllib.request

    if not public_url():
        return False
    address = fixed_address()
    if not address:
        return True                     # a throwaway tunnel: there is nothing local to ask
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=timeout) as answer:
            report = json.loads(answer.read().decode("utf-8", "replace"))
    except Exception:
        return True
    tunnels = report.get("tunnels") if isinstance(report, dict) else None
    if not tunnels:
        return False                    # the agent is up and serving nothing: ours has gone
    return any(address in str(t.get("public_url", "")) for t in tunnels)


def public_url() -> str:
    """The outside address, while a tunnel is up - from memory, or from what was saved."""
    proc = _TUNNEL.get("proc")
    if proc is not None and proc.poll() is None and _TUNNEL.get("url"):
        return str(_TUNNEL["url"])
    url, pid = _remembered()
    if url and _alive(pid):
        return url                 # a tunnel from before JARVIS restarted is still ours
    if url:
        try:
            _tunnel_file().unlink(missing_ok=True)      # it died: don't hand out a dead address
        except OSError:
            pass
    return ""


def unexpose() -> str:
    """Close the tunnel - the page goes back to being reachable only on your own wi-fi."""
    import os
    import signal

    was = bool(public_url())
    proc = _TUNNEL.get("proc")
    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()
    else:
        _url, pid = _remembered()     # started before a restart: still ours to close
        if _alive(pid):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    try:
        _tunnel_file().unlink(missing_ok=True)
    except OSError:
        pass
    handle = _TUNNEL.get("log")
    if handle is not None:
        try:
            handle.close()
        except OSError:
            pass
    _TUNNEL.update(proc=None, url="", log=None)
    return "Closed - the talk page is back to your wi-fi only." if was else "It wasn't shared."


# Android will only install a page as an app if the manifest offers a 192px AND a 512px icon, so
# the reactor is drawn at whatever size is asked for.
ICON_SIZES = (180, 192, 512)
_ICONS: dict = {}


def icon_png(size: int = 192) -> bytes:
    """The home-screen icon: JARVIS's reactor, drawn once per size and kept in memory."""
    size = int(size)
    if size in _ICONS:
        return _ICONS[size]
    try:
        import cv2
        import numpy as np

        unit = size / 180.0
        img = np.zeros((size, size, 3), np.uint8)
        img[:] = (13, 9, 7)
        centre = (size // 2, size // 2)
        for radius, colour, thick in ((78, (90, 60, 20), 6), (62, (221, 168, 41), 3),
                                      (40, (255, 227, 143), 2)):
            cv2.circle(img, centre, int(radius * unit), colour, max(1, int(thick * unit)), cv2.LINE_AA)
        cv2.circle(img, centre, int(26 * unit), (255, 240, 200), -1, cv2.LINE_AA)
        for angle in range(0, 360, 45):
            rad = np.deg2rad(angle)
            a = (int(centre[0] + 30 * unit * np.cos(rad)), int(centre[1] + 30 * unit * np.sin(rad)))
            b = (int(centre[0] + 60 * unit * np.cos(rad)), int(centre[1] + 60 * unit * np.sin(rad)))
            cv2.line(img, a, b, (221, 168, 41), max(1, int(3 * unit)), cv2.LINE_AA)
        ok, buf = cv2.imencode(".png", img)
        _ICONS[size] = buf.tobytes() if ok else b""
    except Exception:
        _ICONS[size] = b""
    return _ICONS[size]


def page_html() -> str:
    """The page, with the model names filled in - which model to ask is not a secret; the key is,
    and that never leaves your phone."""
    try:
        from core.config import load_settings
        settings = load_settings()
        groq = str(settings.get("llm.groq.model", "") or "llama-3.3-70b-versatile")
        gemini = str(settings.get("llm.gemini.model", "") or "gemini-2.5-flash")
    except Exception:
        groq, gemini = "llama-3.3-70b-versatile", "gemini-2.5-flash"
    return PAGE.replace("__GROQ_MODEL__", groq).replace("__GEMINI_MODEL__", gemini)


def token_path():
    from core import oslayer
    return Path(oslayer.user_data_dir()) / "talk_token.txt"


def token() -> str:
    """The secret in the link. Made once and kept, so the same URL keeps working."""
    path = token_path()
    try:
        existing = path.read_text().strip()
        if len(existing) >= 16:
            return existing
    except OSError:
        pass
    fresh = secrets.token_urlsafe(18)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fresh)
        path.chmod(0o600)
    except OSError:
        pass
    return fresh


class TalkServer:
    """Serves the hold-to-talk page and answers what it sends. `ask(text) -> reply`."""

    def __init__(self, ask, port: int = PORT, host: str = "0.0.0.0", secret: str | None = None,
                 transcribe=None, voice=True, emit=None, https: bool = True):
        self.ask = ask
        self.port = int(port)
        self.host = host
        self.https = bool(https)
        self.secret = secret or token()
        self.transcribe = transcribe or self._whisper
        self.voice = bool(voice)
        self.emit = emit or (lambda _text: None)
        self.audio = {}                 # id -> path of a reply waiting to be fetched
        self.httpd = None
        self._thread = None

    # -- the work ---------------------------------------------------------------------------------
    @staticmethod
    def _whisper(raw: bytes) -> str:
        """What was said, transcribed on this machine."""
        import tempfile

        from core import voicenote
        folder = Path(tempfile.gettempdir()) / "jarvis-voice"
        folder.mkdir(mode=0o700, exist_ok=True)
        path = folder / f"talk-{uuid.uuid4().hex[:8]}.webm"
        try:
            path.write_bytes(raw)
            segments, _ = voicenote.model().transcribe(str(path), beam_size=1, language="en",
                                                       vad_filter=False, without_timestamps=True,
                                                       condition_on_previous_text=False)
            return " ".join(s.text for s in segments).strip()
        finally:
            try:
                path.unlink(missing_ok=True)      # your voice isn't kept after it's been read
            except OSError:
                pass

    def answer_text(self, text: str) -> dict:
        """A request in words rather than audio - what your phone answered for itself while this Mac
        was away, and kept for me to actually carry out."""
        request = str(text or "").strip()
        if not request:
            return {"error": "nothing to do"}
        self.emit(f"phone (kept while I was away): {request}")
        _LOCAL.calling = True
        try:
            reply = str(self.ask(request) or "").strip() or "Done, sir."
        finally:
            _LOCAL.calling = False
        return {"heard": request, "text": reply}

    def answer(self, raw: bytes) -> dict:
        """Audio in, answer out - the whole turn, with no HTTP in sight (so it can be tested)."""
        started = time.monotonic()
        self.emit(f"call: {len(raw) // 1024} KB of audio from the phone")
        heard = ""
        try:
            heard = str(self.transcribe(raw) or "").strip()
        except Exception as exc:
            self.emit(f"call: couldn't read the audio - {exc}")
            return {"error": f"I couldn't make out the audio: {exc}"}
        self.emit(f"call: heard {heard!r} in {time.monotonic() - started:.1f}s")
        if not heard:
            return {"heard": "", "text": "I didn't catch that, sir."}
        from core.voice import parse_wake
        _, stripped = parse_wake(heard)           # "Jarvis, ..." is optional here - you pressed talk
        request = stripped or heard
        self.emit(f"phone: {request}")
        _LOCAL.calling = True
        try:
            reply = str(self.ask(request) or "").strip() or "Done, sir."
        finally:
            _LOCAL.calling = False
        self.emit(f"call: answered in {time.monotonic() - started:.1f}s")
        out = {"heard": heard, "text": reply}
        if self.voice:
            from core import speechfile
            path = speechfile.to_audio(reply)
            if path:
                ident = uuid.uuid4().hex[:12]
                self.audio[ident] = path
                out["audio"] = f"/audio/{ident}"
        return out

    def take_audio(self, ident: str):
        """Hand over a reply's audio once, then forget it."""
        path = self.audio.pop(str(ident), None)
        if not path or not Path(path).exists():
            return None, None
        data = Path(path).read_bytes()
        from core import speechfile
        speechfile.cleanup(path)
        return data, ("audio/mp4" if str(path).endswith(".m4a") else "audio/wav")

    # -- the server -------------------------------------------------------------------------------
    def start(self) -> str:
        if self.running():
            return self.url()
        server = self
        pageurl = self.url()

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):        # no request spam in the panel
                pass

            def _allowed(self):
                query = parse_qs(urlparse(self.path).query)
                given = (query.get("k") or [""])[0] or self.headers.get("X-JARVIS-Token", "")
                return secrets.compare_digest(str(given), server.secret)

            def _send(self, code, body, kind="text/plain; charset=utf-8"):
                payload = body if isinstance(body, bytes) else str(body).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                route = urlparse(self.path).path
                if not self._allowed():
                    return self._send(403, "Not for this device.")
                if route in ("/", "/index.html"):
                    return self._send(200, page_html(), "text/html; charset=utf-8")
                if route == "/manifest.webmanifest":
                    start = f"/?k={server.secret}&call=1"
                    icons = [{"src": f"/icon-{size}.png?k={server.secret}",
                              "sizes": f"{size}x{size}", "type": "image/png",
                              "purpose": "any maskable"} for size in (192, 512)]
                    body = json.dumps({"id": "jarvis-call", "name": "JARVIS", "short_name": "JARVIS",
                                       "description": "Talk to JARVIS", "start_url": start,
                                       "scope": "/", "display": "standalone", "orientation": "portrait",
                                       "background_color": "#07090d", "theme_color": "#07090d",
                                       "prefer_related_applications": False, "icons": icons})
                    return self._send(200, body, "application/manifest+json")
                if route.startswith("/icon"):
                    asked = "".join(ch for ch in route if ch.isdigit())
                    return self._send(200, icon_png(int(asked or 192)), "image/png")
                if route == "/alive":       # the page asks this the moment it opens
                    return self._send(200, "awake")
                if route == "/sw.js":
                    return self._send(200, WORKER, "application/javascript")
                if route.startswith("/audio/"):
                    data, kind = server.take_audio(route.rsplit("/", 1)[-1])
                    return self._send(200, data, kind) if data else self._send(404, "gone")
                return self._send(404, "no such page")

            def do_POST(self):
                if not self._allowed():
                    return self._send(403, "Not for this device.")
                route = urlparse(self.path).path
                if route == "/say":         # words, not audio: what your phone kept while I was off
                    length = min(int(self.headers.get("Content-Length") or 0), 4000)
                    text = self.rfile.read(length).decode("utf-8", "replace") if length else ""
                    return self._send(200, json.dumps(server.answer_text(text)),
                                      "application/json")
                if route != "/ask":
                    return self._send(404, "no such page")
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return self._send(400, "no audio")
                if length > 4 * MAX_AUDIO:
                    return self._send(413, "that's far too much audio")
                server.emit(f"call: receiving {length // 1024} KB")
                raw = self.rfile.read(length)   # read it all, then judge: cutting the phone off
                if len(raw) > MAX_AUDIO:        # mid-upload just looks like a broken connection
                    return self._send(413, "that's too much audio")
                try:
                    out = server.answer(raw)
                except Exception as exc:
                    out = {"error": str(exc)}
                self._send(200, json.dumps(out), "application/json")

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        self.port = int(self.httpd.server_address[1])     # port 0 means "any free one" - report it
        if self.https:
            cert, key = certificate()
            if cert and key:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(str(cert), str(key))
                self.httpd.socket = context.wrap_socket(self.httpd.socket, server_side=True)
            else:
                self.https = False
                self.emit("I couldn't make a certificate, so the page is plain http - a phone won't "
                          "give it the microphone.")
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True,
                                        name="jarvis-talk")
        self._thread.start()
        return self.url() if pageurl.endswith(":0/?k=" + self.secret) else pageurl

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def running(self) -> bool:
        return self.httpd is not None and self._thread is not None and self._thread.is_alive()

    def url(self) -> str:
        """The address to open on the phone (its own network address, not 0.0.0.0)."""
        where = lan_ip() if self.host in ("0.0.0.0", "") else self.host
        return f"{'https' if self.https else 'http'}://{where}:{self.port}/?k={self.secret}"


# ---- one server per JARVIS ----------------------------------------------------------------------

_ACTIVE: TalkServer | None = None
_HANDLERS: dict = {}


def configure(ask, emit=None, settings=None) -> None:
    _HANDLERS.update(ask=ask, emit=emit or (lambda _t: None), settings=settings)


def start() -> str:
    global _ACTIVE
    if not _HANDLERS:
        return "The talk page needs the JARVIS panel running."
    if _ACTIVE is not None and _ACTIVE.running():
        return f"Already listening on {_ACTIVE.url()}"
    settings = _HANDLERS.get("settings")
    _ACTIVE = TalkServer(_HANDLERS["ask"], port=int(settings.get("talk.port", PORT) if settings else PORT),
                         host=str(settings.get("talk.host", "0.0.0.0") if settings else "0.0.0.0"),
                         https=bool(settings.get("talk.https", True) if settings else True),
                         emit=_HANDLERS.get("emit"))
    url = _ACTIVE.start()
    return (f"Talk to me from your phone: {url}\n"
            "Same wi-fi as this Mac. Your phone will warn that the certificate isn't trusted - it's "
            "this Mac's own; accept it once and the microphone works. Press Call and just talk.\n"
            "Share > Add to Home Screen puts a JARVIS icon on your phone that opens straight into "
            "a call.")


def stop() -> str:
    """Close the page - and the tunnel with it: a public address pointing at a page that no longer
    exists is worse than useless."""
    global _ACTIVE
    shared = unexpose() if public_url() else ""
    if _ACTIVE is not None and _ACTIVE.running():
        _ACTIVE.stop()
        _ACTIVE = None
        return "Talk page closed." + (" " + shared if shared else "")
    return "The talk page wasn't running." + (" " + shared if shared else "")


def running() -> bool:
    return _ACTIVE is not None and _ACTIVE.running()


def url() -> str:
    return _ACTIVE.url() if running() else ""
