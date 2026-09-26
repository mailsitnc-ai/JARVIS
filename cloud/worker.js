/* JARVIS where nothing can switch it off.
 *
 * This runs on Cloudflare's free plan - no card, no server, no machine to keep awake. It answers
 * your WhatsApp whether or not the Mac is on, remembers the conversation, and puts anything that
 * genuinely needs the Mac into a queue that the Mac drains the moment it wakes up.
 *
 * What it needs, all set in the dashboard (see cloud/README.md):
 *   secrets:  GROQ_KEY  WA_TOKEN  WA_PHONE_ID  VERIFY  LINK_SECRET  ALLOWED
 *   KV:       JARVIS  (a namespace bound under this name)
 */

const GRAPH = 'https://graph.facebook.com/v21.0';
const MODEL = 'openai/gpt-oss-120b';
const TURNS = 12;                    /* how much of the conversation is remembered */
const RULES =
  "You are JARVIS, Shivam's assistant. You address him as sir, briefly and drily - never servile. " +
  "You are running in the cloud, so you cannot see or touch his Mac right now. Answer in at most " +
  "three short sentences, no lists, no markdown. If what he asks needs his Mac (opening apps or " +
  "files, screenshots, typing, browsing, anything on the laptop), reply with exactly LAPTOP: " +
  "followed by his request in plain words and nothing else.";

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;
    try {
      if (path === '/wa' && request.method === 'GET') return verify(url, env);
      if (path === '/wa' && request.method === 'POST') return whatsapp(request, env);
      if (path === '/jobs') return jobs(request, url, env);
      if (path === '/ping') return text('awake');
      return text('no such page', 404);
    } catch (err) {
      console.log('failed: ' + err.stack);
      return text('ok');            /* never make Meta retry: it would do everything twice */
    }
  }
};

const text = (body, status = 200) => new Response(body, {status, headers: {'content-type': 'text/plain'}});
const json = (body, status = 200) => new Response(JSON.stringify(body), {status, headers: {'content-type': 'application/json'}});
const digits = (s) => String(s || '').replace(/\D/g, '');

/* Meta proves the address is yours once, by asking for a word you chose. */
function verify(url, env) {
  const asked = url.searchParams;
  if (asked.get('hub.mode') === 'subscribe' && asked.get('hub.verify_token') === env.VERIFY) {
    return text(asked.get('hub.challenge') || '');
  }
  return text('no', 403);
}

/* ---- a message arrives ------------------------------------------------------------------------ */

async function whatsapp(request, env) {
  const payload = await request.json().catch(() => ({}));
  const messages = read(payload);
  /* Answer Meta immediately and do the thinking afterwards: it re-sends anything it is not
     promptly told arrived, and a slow reply becomes the same request carried out twice. */
  for (const message of messages) await handle(message, env);
  return text('ok');
}

function read(payload) {
  const out = [];
  for (const entry of payload.entry || []) {
    for (const change of entry.changes || []) {
      for (const message of (change.value || {}).messages || []) {
        let body = '';
        if (message.type === 'text') body = ((message.text || {}).body || '').trim();
        else if (message.type === 'interactive' || message.type === 'button') {
          const inner = message[message.type] || {};
          body = String(inner.text || (inner.button_reply || {}).title || (inner.list_reply || {}).title || '').trim();
        }
        if (body) out.push({id: message.id, from: digits(message.from), text: body});
      }
    }
  }
  return out;
}

async function handle(message, env) {
  const allowed = String(env.ALLOWED || '').split(',').map(digits).filter(Boolean);
  if (allowed.length && !allowed.includes(message.from)) return;
  if (await env.JARVIS.get('seen:' + message.id)) return;         /* a re-delivery, not a new order */
  await env.JARVIS.put('seen:' + message.id, '1', {expirationTtl: 3600});

  const history = JSON.parse((await env.JARVIS.get('history:' + message.from)) || '[]');
  let answer = await think(message.text, history, env);

  if (/^LAPTOP:/i.test(answer)) {
    const job = answer.replace(/^LAPTOP:\s*/i, '').trim();
    await queue(job, message.from, env);
    answer = "That one's for the Mac, sir. It's on the list and I'll run it the moment it's awake.";
  }
  history.push({role: 'user', content: message.text}, {role: 'assistant', content: answer});
  await env.JARVIS.put('history:' + message.from, JSON.stringify(history.slice(-TURNS)),
                       {expirationTtl: 86400});
  await say(answer, message.from, env);
}

async function think(request, history, env) {
  const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
    method: 'POST',
    headers: {'content-type': 'application/json', authorization: 'Bearer ' + env.GROQ_KEY},
    body: JSON.stringify({
      model: env.MODEL || MODEL, max_tokens: 400,
      messages: [{role: 'system', content: RULES}, ...history, {role: 'user', content: request}]
    })
  });
  if (!res.ok) return 'My thinking is out of reach for the moment, sir (' + res.status + ').';
  const body = await res.json();
  return ((body.choices || [{}])[0].message || {}).content?.trim()
         || "I've nothing useful to say to that, sir.";
}

async function say(body, to, env) {
  await fetch(GRAPH + '/' + env.WA_PHONE_ID + '/messages', {
    method: 'POST',
    headers: {'content-type': 'application/json', authorization: 'Bearer ' + env.WA_TOKEN},
    body: JSON.stringify({messaging_product: 'whatsapp', to: digits(to), type: 'text',
                          text: {body: String(body).slice(0, 3900)}})
  });
}

/* ---- the queue the Mac drains ------------------------------------------------------------------ */

async function queue(job, from, env) {
  const all = JSON.parse((await env.JARVIS.get('queue')) || '[]');
  all.push({id: crypto.randomUUID(), text: job, from: from, at: Date.now()});
  await env.JARVIS.put('queue', JSON.stringify(all.slice(-50)));
}

/* The Mac asks for work with GET, and reports what it did with POST. Both need the shared secret -
   this is the one door into your Mac's to-do list. */
async function jobs(request, url, env) {
  if (url.searchParams.get('s') !== env.LINK_SECRET) return text('no', 403);
  if (request.method === 'GET') {
    const all = JSON.parse((await env.JARVIS.get('queue')) || '[]');
    if (all.length) await env.JARVIS.put('queue', '[]');
    return json({jobs: all});
  }
  if (request.method === 'POST') {
    const done = await request.json().catch(() => ({}));
    if (done.text && done.to) await say(done.text, done.to, env);
    return json({ok: true});
  }
  return text('no such method', 405);
}
