# JARVIS in the cloud

`worker.js` is a JARVIS that nothing can switch off. It runs on Cloudflare's free plan — no card, no
server, no machine to keep awake — and answers your WhatsApp whether or not the Mac is on. Anything
that genuinely needs the Mac is written down instead of refused, and the Mac carries it out the next
time it is awake (`core/cloudlink.py`).

    your phone  ──WhatsApp──>  Meta  ──webhook──>  this worker  ──>  Groq
                                                        │
                                              "that one needs the Mac"
                                                        │
                                                    a queue  <──asks──  your Mac, whenever it's up

## Once, in the Cloudflare dashboard

1. **Sign up** at dash.cloudflare.com (email and password; no card).
2. **Storage & Databases → KV → Create**, call it `jarvis`.
3. **Workers & Pages → Create → Start with Hello World → Deploy**, then **Edit code**: paste
   `worker.js` over what's there and Deploy. Note the address it gives you —
   `https://<name>.<you>.workers.dev`.
4. **Settings → Bindings → Add → KV namespace**: variable name `JARVIS`, namespace `jarvis`.
5. **Settings → Variables and Secrets** — add these as **Secret** (not plain text):

   | Name | What it is |
   |---|---|
   | `GROQ_KEY` | your Groq key — the thinking |
   | `WA_TOKEN` | the WhatsApp access token from the Meta app |
   | `WA_PHONE_ID` | the phone number ID from the Meta app |
   | `VERIFY` | any word you choose; Meta asks for it once |
   | `LINK_SECRET` | any long random string; this is what your Mac proves itself with |
   | `ALLOWED` | your own number, digits only — nobody else may command JARVIS |

## Once, in the Meta app

Webhooks → Configure: callback URL `https://<your-worker>/wa`, verify token = your `VERIFY`.
Subscribe to **messages**. Send yourself a message to check it answers.

## Once, on the Mac

    jarvis setkey cloud                       # paste the same LINK_SECRET
    jarvis config --set cloud.address=https://<your-worker>
    jarvis config --set cloud.enabled=true

From then on the Mac picks up whatever the cloud left for it, every 20 seconds it is awake, and the
answer goes back to your phone on WhatsApp.

## What lives where

| | Cloud | Mac |
|---|---|---|
| Answers WhatsApp | always | — |
| Remembers the conversation | yes (a day) | — |
| Opens apps, files, the browser, types | no | yes, when awake |
| The 49 skills, evolution, Google | no | yes |

The free plan allows 100,000 requests a day. A busy day of messages and the Mac asking for work every
20 seconds comes to roughly 5,000.
