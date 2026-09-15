# JARVIS 1.0 for Windows

A self-evolving personal assistant. Press **Ctrl+Alt+J** and the window you were working in snaps to 60% of
the screen while JARVIS docks into the other 40%. Ask for something it can't do yet and it writes the
skill itself, tests it in a sandbox, installs it, and answers you in the same turn.

Standard library only: no pip installs, nothing extra loaded into memory.

## Quick start

```powershell
.\install.ps1 -AddToPath        # finds Python, runs the tests, adds `jarvis` to PATH
ollama pull qwen2.5-coder:0.5b-instruct-q8_0   # the local model, 531 MB, one time
jarvis doctor --live            # model, skills, hotkey, daemon, plus a real local round trip
jarvis on                       # open the 60/40 split; Ctrl+Alt+J toggles from then on
jarvis startup enable           # optional: keep the hotkey alive from sign-in
```

## Commands

| Command | What it does |
| --- | --- |
| `jarvis` / `jarvis toggle` | Show or hide the 60/40 split (starts JARVIS if needed) |
| `jarvis on` / `jarvis off` / `jarvis stop` | Show, hide (restores your window), quit |
| `jarvis interrupt` | Stop whatever JARVIS is currently doing (or press Ctrl+Alt+C) |
| `jarvis ask "..."` | One request, answered in the terminal |
| `jarvis chat` | Terminal conversation |
| `jarvis evolve "..."` | Build a new skill even if an existing trigger matches |
| `jarvis skills -v` | Installed skills and their triggers |
| `jarvis doctor [--live]` | Health check |
| `jarvis config [--set k=v] [--unset k]` | Show or change settings |
| `jarvis permissions [cap] [allow\|deny\|ask]` | Show or change what JARVIS may do on your PC |
| `jarvis autonomy on\|off\|status` | Unleash it: act without asking, skip plan review, allow risky calls |
| `jarvis improve <skill> "<reason>"` | Have JARVIS rewrite one of its own evolved skills |
| `jarvis edit-self "<instruction>"` | Have JARVIS rewrite its own source (tested, auto-rollback) |
| `jarvis startup enable\|disable\|status` | Start at Windows sign-in |

## Controlled access to your PC

JARVIS has full access to your laptop but can't use it until you say so. Every action that touches
the computer — opening apps, files or websites, capturing the screen, reading or writing files,
running commands — goes through one broker that stops and asks first:

```
You  take a screenshot
     JARVIS wants to: Capture the screen        [ Allow once ] [ Always allow ] [ Deny ]
```

- **Allow once** does it this time. **Always allow** does it and stops asking for that capability.
  **Deny** refuses; JARVIS says so and moves on (it is not treated as an error).
- Capabilities, each `ask` (the default) / `allow` / `deny`: `open`, `screen`, `read_files`,
  `write_files`, `run_command`, `network`, `packages`, `notify` (popups), `google`, `self_edit` (rewrite its
  own code). Grants are saved in `%APPDATA%\JARVIS\permissions.json`.
- Change them any time: `jarvis permissions screen allow`, `jarvis permissions run_command deny`,
  `jarvis permissions --reset`, or `/permissions` in the panel.

### Autonomy mode — the "unleash it" switch

When you want it to just *go*, flip one switch:

```
jarvis autonomy on      # or the 🔥 button in the panel header, or /autonomy on
```

While autonomy is **on**, every capability reads as allowed: JARVIS acts without ever stopping to
ask, skips the plan-approval step on multi-step tasks, and lets evolved skills use risky calls
(deleting files, running shell commands, `eval`) that are otherwise blocked. It persists across
restarts, and `jarvis autonomy off` (or `jarvis permissions --reset`) puts the gate back and restores
your per-capability grants exactly as they were. This is the honest version of "full access": the
power is real, and the off-switch is yours.

Skills never call the OS directly — they use `context["actions"]` (open_app, open_url, screenshot,
read_file, write_file, list_dir, run_command), which is exactly what the gate sits on. Evolved skills
are held to this too: the sandbox rejects a draft that imports `subprocess`/`webbrowser` or calls
`os.startfile`. During verification the broker is in dry-run, so an action-skill is tested without
doing anything real.

## Layout

```
core/              orchestrator.py (routing), llm_router.py (local-first Ollama), skill_loader.py (hot reload),
                   actions.py (the action broker), permissions.py (per-capability grants),
                   config.py, keystore.py (DPAPI), contract.py (the skill contract), cli.py
skills/            one .py per skill: SKILL = {name, description, triggers} + run(request, context)
memory/            store.py; data/interactions.jsonl and data/evolution_log.jsonl (TF-IDF recall)
evolution_engine/  analyzer.py > drafter.py > sandbox.py (+ sandbox_harness.py) > engine.py; archive/ keeps replaced versions
window_manager/    win32.py (ctypes), split.py (60/40), hotkey.py (RegisterHotKey), ipc.py (terminal control)
ui/panel.py        the Tk workspace, the skills manager, and the background daemon
ui/reactor.py      the animated arc-reactor widget (idle/busy/speaking/error states)
tests/             python -m unittest discover -s tests -t .
```

Built-in skills: `temperature_converter`, `calculator`, `current_time`, `system_status`, `open_app`,
`web_search`, `browser_tab`, `screenshot`, `show_screenshot`, `speak`.

## Chat, use a skill, or build one

JARVIS decides what each message *is* before acting, so it can hold a conversation instead of
turning every sentence into a skill:

- **Use a skill** — a trigger matches (or a correction resolves to one). If the best skill passes,
  the next is tried.
- **Chat** — greetings, thanks, "who are you", "what can you do", opinions, and questions with no
  action verb get a conversational reply. The panel remembers recent turns, so follow-ups and
  corrections have context.
- **Build a skill** — only an *actionable* request (one with a verb like open, count, convert,
  screenshot, download...) that no skill covers goes to the evolution engine. Building takes ~25-80s
  on this CPU, so the panel **acknowledges immediately and builds in the background** ("On it -
  building..."); you can keep chatting and using other skills while it works, and the result appears
  when it's ready. (`Jarvis.submit()` returns a background job for builds; `Jarvis.handle()` stays
  synchronous for the CLI.) Builds run one at a time.

How it tells them apart (deterministic, so it's fast and predictable on a small model):

- **Politeness and corrections are peeled off** before routing: "can you open notepad" and
  "no i meant open calculator" both route straight to the right skill — no wrong build.
- **Small talk** ("thanks", "never mind", "what can you do", "who are you") gets an instant canned reply.
- **A task is anything with an action/compute verb** (open, type, say, read aloud, screenshot,
  calculate, download...). Everything else is chat. This is deterministic on purpose: a 0.5B model
  classifying chat-vs-command proved unreliable (it once called "tell me a joke" a command) and slow.
- **Only self-code-editing is refused** ("edit your own source", "change your prompt"). "Give yourself
  the ability to X" is *not* refused — that's the point of a self-evolving assistant, so it builds a
  skill for X. `jarvis evolve "..."` forces a build either way.

JARVIS can also **speak** (Windows text-to-speech): "say hello", "read this aloud: ...".

**Skills manager:** the **⚙ skills** button (top-right of the panel) opens a window listing every skill -
builtin and evolved - where you can read a skill's description/triggers, **view its source**, **disable/enable**
it, and **delete** evolved ones (a copy is archived). Disabling renames the file so the loader skips it.

## The learning loop

JARVIS improves from use, and it survives restarts (stored in `memory/data/`):

- **Learned routes.** When a phrasing has no trigger but resolves to an existing skill, JARVIS remembers
  that phrasing → skill, so next time it routes straight there instead of rebuilding. You can also teach
  it directly: `jarvis teach speak "read me the news"`, or `/teach speak read me the news` in the panel.
- **Lessons.** Failed builds and skill crashes are recorded as short lessons, which are fed into the
  skill-drafting prompt so the model stops repeating the same mistake.
- **See it / reset it:** `jarvis lessons` (or `/lessons`) shows what it has learned; `jarvis lessons --clear`
  forgets everything. `jarvis doctor` shows the counts.

It's deliberately conservative — it learns "this phrase means that skill" and "avoid this past mistake",
not free self-rewriting, because those are the signals that are safe and useful on a small model.

## Working smarter, not just evolving

The 0.5B model is small, so most of the intelligence is in routing, not generation — this keeps common
things instant and stops the model building junk:

- **Multi-step requests are chained**, with no model call: "open calculator **and** do 1+1" opens
  Calculator and computes `2`. It only splits when each step maps to a different existing skill.
- **When a cloud model is active, it drives understanding.** Anything that isn't an instant obvious
  command goes through one LLM "understand" call (`core/understand.py`) that decides intent:
  - **chat** — it answers in words;
  - **act** — a one-shot task runs straight away; a multi-step one becomes a goal it **works through
    step by step** (see the agentic loop below), so "open google docs and find the budget file" and
    vague references actually work;
  - **preference** — it changes a setting for good: "use gemini as my main model", "switch back to groq",
    "put JARVIS on the left" — applied live and persisted.
  On local-only (the 0.5B) this stays off and the deterministic rules run instead, since a small model
  can't classify reliably.

For a task it has no skill for, it **builds one and runs it**, including reaching outside itself:
- **Live data** off the internet (gated `network`): "what's the price of bitcoin" → it writes a skill
  that calls a crypto API and answers with the real number.
- **Third-party packages** (gated `packages`): if a new skill needs a library, it declares it in
  `SKILL["requires"]`, JARVIS `pip install`s it, then verifies and runs — e.g. "make an ascii qr code for
  hi" installs `qrcode` and prints a real, scannable code. A missing import it forgot to declare is
  auto-installed and re-verified.

## The agentic loop (act → observe → adapt)

A multi-step task isn't run as a fixed script. JARVIS turns it into a **goal** and pursues it one step at
a time (`core/agent.py`), and this is where it gets to genuinely *figure things out*:

1. It first shows you the suggested plan and waits for **one approval** (deny = nothing runs).
2. Then it does a single step through the normal machinery — an existing skill, or one it **builds on the
   spot** — so each step inherits permissions, network, package install and self-repair.
3. It reads the **real result** of that step and asks the model what to do next. If a step failed or
   returned something unexpected, it **adapts** — tries a different instruction instead of blindly
   continuing — until the goal is met, then writes the final answer from what it gathered.

So "get the price of bitcoin then convert it to euros" runs the fetch, sees the actual number, and only
then decides the conversion step. Guardrails: it stops if it starts repeating the same step, and never
exceeds `agent.max_steps` (default 5). Cloud-model only, like the rest of understanding.

**Stop it any time — Ctrl+Alt+C.** A running agent loop, a build, or an adaptive retry checks for an
interrupt between steps and bails out cleanly ("Stopped."). It's cooperative, so it stops at the next
checkpoint rather than mid-network-call. Also available as `jarvis interrupt`, the control channel, or in
the panel (the footer shows the shortcut). Change it with `window.interrupt_hotkey`.

- **Reuse before rebuild.** If a request needs a capability an existing skill already covers, JARVIS
  reuses that skill instead of evolving a near-duplicate (no more `web_search_2`, `calculator_2`).
- **`open_app` is thorough**: it checks known apps, your PATH, the registry's App Paths, and your Start
  Menu shortcuts — so "open antigravity" opens an installed app. It also knows **web apps** ("open google
  docs", "gmail", "youtube", any domain) and ignores a trailing "on my chrome". If it genuinely isn't
  found, it says so instead of spawning a web-search clone.
- **It writes code and makes apps.** "write a python program that prints the fibonacci numbers and save it
  as fib.py", "make a tkinter clock app and open it", "create an html landing page" — the `write_file`
  skill has the model generate the file's contents, saves it (gated by `write_files`), and can open/run
  what it just wrote. Combined with evolution, it builds real, runnable programs on your desktop.
- **It shows popups.** "show a popup saying the build is done", "alert me when it's finished" → a real
  Windows message box (the `popup` skill / `notify` action, gated by `notify`). Evolved skills can use
  `context["actions"].notify(...)` too.
- **It remembers the file it just made.** After it writes a file, "**open it**" / "**run it**" / "open the
  file you just made" opens exactly that file (the `open_last` skill; the broker records the last file
  written in `%APPDATA%\JARVIS`, so it works even across separate commands).
- **Skills compose.** A skill can call another with `context["run"]("...")`, so abilities build on each
  other instead of being reimplemented (depth-capped, existing skills only, no builds).
- **Declines cascade.** If the best-matching skill passes on a request, the next match is tried before
  evolution — so "show me the screenshot" opens the last one instead of taking a new one.
- **It can rewrite its own source, on command** (see below) - "rewrite your own X" edits a core file, not
  a skill. "give yourself the ability to X" (a new task) still becomes a skill via evolution.
- **Draft length is capped**, so a rambling draft can't burn minutes before the sandbox rejects it.

## Self-editing (rewriting its own code)

Beyond adding and improving skills, JARVIS can rewrite its **own source files** when you tell it to:

```
rewrite your core/understand.py so it also handles "remind me" as an action
```
or from a terminal: `jarvis edit-self "add a /uptime line to core/cli.py doctor output"`.

Because editing the running program's own code is dangerous, every self-edit goes through a safety harness
(`evolution_engine/self_edit.py`):

1. it picks the one file the request names (or lets the model choose from its editable files),
2. drafts the **complete** new file for your purpose,
3. **rejects it unless it parses** as valid Python,
4. backs the original up (to `_self_edits/`), writes the new version,
5. **runs the whole test suite - and rolls the file straight back if anything fails,**
6. **commits the change to git** (starting a repo if there isn't one), so every self-edit is one revertible
   commit - the reply tells you the hash and the `git revert <hash>` to undo it.

So a self-edit only sticks if it compiles and the tests still pass, and once it does you can always walk it
back with git. Core changes take effect on the next restart (the live process keeps the old code in memory);
skill files hot-reload as usual. It's gated by the `self_edit` permission (allowed while autonomy is on); a
few files (`keystore.py`, `permissions.py`, the self-editor and the sandbox) are **protected** and never
rewritten. Turn off the safety net - at your own risk - with `self_edit.run_tests=false`, or the git commits
with `self_edit.git_commit=false`.

## The evolution loop

1. **Route.** Regex triggers pick the skill. No match at all means evolution, with no LLM call: small models
   rarely answer "none" and would pick a wrong skill. The LLM only breaks ties between matching skills.
2. **Analyze.** The LLM decides if this needs code (a skill) or is just a question (answered directly),
   and returns a name, triggers, test inputs and a plan.
3. **Draft.** The LLM writes the module against the contract in `core/contract.py`.
4. **Verify.** Static checks reject syntax errors, code that runs on import, `input()`, file deletion,
   `eval`/`exec`, `ctypes` and `shell=True`. Then the skill runs in a separate `python -I` process with its own temp
   folder, no API keys in its environment, a 256 MB Job Object memory cap and a 20 s timeout, on every test
   input with `dry_run=True`. Its triggers must match your original request.
5. **Retry.** Any failure goes back to step 3 with the exact error, up to 3 attempts.
6. **Install.** Atomic write into `skills/`, hot-reload, run your original request.
7. **Self-repair (adaptive).** If an evolved skill later crashes *or* returns a swallowed error ("could not
   be imported", a traceback, "failed to..."), JARVIS detects it, feeds the failure back into the loop, and
   rebuilds and re-runs - up to twice - until it actually works. A missing package it discovers this way is
   installed and re-verified. The old version is archived.
8. **Self-improvement (on demand).** It doesn't only *add* skills - it rewrites the ones it already has.
   Say "**improve your `<name>` skill** so it also shows X" (or run `jarvis improve <name> "<reason>"`) and
   JARVIS reads that skill's own source, redrafts it to be better, verifies the new version in the sandbox,
   and only hot-swaps if the improvement passes - otherwise the current skill is left untouched. Built-in
   skills are off-limits; it only rewrites what it evolved itself. This closes the loop: build → run →
   observe → *improve*.

The sandbox protects against broken or careless drafts. It is not a security boundary against hostile code -
and with autonomy on, evolved skills may use risky calls, so the sandbox stops being a wall at all. Use
autonomy when you want raw capability and accept that trade.

### Safeguards for a small local model

Each of these fixes a failure actually measured with the 0.5B model on this laptop:

- **Worked examples instead of templates.** The model copied template placeholders verbatim ("snake_case_skill_name").
- **Length-capped JSON schema.** It otherwise repeated list items until it ran out of tokens.
- **Code decides skill or answer.** It labelled "count the vowels" a plain question, then answered it wrongly.
- **Trigger cleanup.** Values ("banana"), catch-alls (`.*`) and lone generic verbs ("generate") are dropped.
- **Mechanical draft repair.** Example usage at the bottom is removed, a missing `run()` entry point is added,
  and the analysed name and triggers are written into `SKILL`. Redrafting cost 20 s and repeated the mistake.
- **Warmer retries.** At a fixed temperature each retry re-emitted the same broken draft.

The sandbox proves a skill runs, not that its answers are right, and a 0.5B model makes mistakes. Glance at new
files in `skills/` (each starts with an "Evolved by JARVIS" line). For example, the first password skill it wrote
used `random` instead of `secrets` and ignored the requested length; it was fixed by hand.

## Brains: a cloud chain with a local fallback

JARVIS routes every model call through a fallback chain (`llm.fallback_order`), trying each provider until
one answers. Cloud providers only activate once you add a key; until then everything falls through to local.

| Order | Provider | Model | Cost | Notes |
| --- | --- | --- | --- | --- |
| 1 | **Groq** | `openai/gpt-oss-120b` | free, ~1,000/day | ~500 tok/s; skills in seconds |
| 2 | **Gemini** | `gemini-2.5-flash` | free, no card | strong reasoning; ~15 req/min |
| 3 | **Ollama** | `qwen2.5-coder:0.5b-instruct-q8_0` | free, local, offline | always-available fallback |

Both cloud tiers are free (no card); their limits stack, and Ollama covers offline / rate-limit-exhausted.
Add whichever keys you want (each is pasted by you and DPAPI-encrypted):

```powershell
jarvis setkey groq       # free key: console.groq.com
jarvis setkey gemini     # free key: aistudio.google.com/apikey
jarvis doctor            # shows the chain and which provider the next call uses
```

Reorder or pin any time: `jarvis config --set llm.fallback_order=[gemini,groq,ollama]`, or set one model with
`jarvis config --set llm.gemini.model=gemini-3.5-flash`. **Privacy:** free cloud tiers generally train on your
prompts — keep private files/secrets on the local model (it's the fallback and never leaves the PC).

## Google Drive & Gmail

JARVIS can search your real Google Drive and Gmail (read-only) once you connect an account. Because it
runs on your machine, you supply your own free OAuth client (one-time), then log in:

```powershell
jarvis google setup     # paste a Client ID + secret from a Google Cloud "Desktop app" OAuth client
jarvis google login     # opens the browser; sign in and consent (Drive + Gmail, read-only)
jarvis google status    # check the connection
```

To make the OAuth client: Google Cloud Console → new project → enable **Google Drive API** and **Gmail
API** → OAuth consent screen (External; add your own email as a **Test user**) → Credentials → OAuth
client ID → **Desktop app** → copy the Client ID and secret. Tokens are stored DPAPI-encrypted; JARVIS
only ever reads/searches (scopes `drive.readonly`, `gmail.readonly`).

Then just ask: "find the budget file in my drive", "any emails from alice about the invoice". Access is
gated by the `google` permission like everything else, and it stays local otherwise.

## The local model

The local fallback runs through [Ollama](https://ollama.com): free, offline, private. The model is
`qwen2.5-coder:0.5b-instruct-q8_0` (531 MB), a code-specialised model at full 8-bit precision, picked as the
middle ground for 3.9 GB of RAM:

| Model | Download | Coding score (HumanEval / MBPP) | Fit for this laptop |
| --- | --- | --- | --- |
| qwen2.5-coder:0.5b | 398 MB | 61.6 / 52.4 (before 4-bit loss) | lightest, slightly less accurate |
| **qwen2.5-coder:0.5b-instruct-q8_0** | **531 MB** | **61.6 / 52.4** | **default: about half the RAM of the 1.5B** |
| qwen2.5-coder:1.5b | 986 MB | 70.7 / 69.2 | smarter; close Chrome while it builds skills |
| gemma3:1b | 815 MB | 41.5 / 35.2 | worse at code than the 0.5B coder |

Measured on this laptop (i5-3210M, 3.9 GB RAM, CPU only):

| Step | Time |
| --- | --- |
| Loading the model and first reply | about 6 s |
| Reading a prompt / writing a reply | about 48 / 20 tokens per second |
| Analysing a missing capability | 7 to 12 s |
| Drafting, sandbox-verifying and installing the skill | about 15 s |
| **A brand-new skill, end to end** | **about 25 s** ("count the vowels", "generate a password": both first try) |
| Reusing an installed skill | 0.3 s, no model call |

While loaded the model uses about 590 MB of RAM (500 MB weights, 48 MB context cache, 40 MB compute).

How JARVIS keeps it light:
- Ollama settings (user environment variables): `OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`,
  `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_KEEP_ALIVE=3m`.
- The model unloads after 3 idle minutes, so the RAM comes back when you're not using JARVIS.
- JARVIS starts `ollama serve` itself when needed, so Ollama doesn't have to run from sign-in.
- Replies stream, so the panel shows progress instead of freezing, and warns when other apps leave too little RAM.
- Skill analysis uses Ollama's structured output, so even a small model returns valid JSON.
- Existing skills never touch the model: only new skills and plain questions do.

Switch to the smarter model any time, no rebuild:

```powershell
ollama pull qwen2.5-coder:1.5b
jarvis config --set llm.ollama.model=qwen2.5-coder:1.5b
```

## Settings

Defaults live in `core/config.py`, overridden by `%APPDATA%\JARVIS\config.json`, then by `JARVIS_SECTION__KEY`
environment variables. Useful ones: `window.hotkey`, `window.interrupt_hotkey`, `window.split`, `window.jarvis_side`,
`evolution.max_attempts`, `evolution.allow_risky_calls`, `llm.groq.model`, `llm.ollama.model`,
`agent.max_steps` (how many steps the agentic loop may take, default 5).

The shortcut is Ctrl+Alt+J because on a French AZERTY keyboard Ctrl+Alt is AltGr, and AltGr+J types nothing.
Apps running as administrator can't be resized by a normal process; run JARVIS elevated if you need that.
The daemon logs to `%APPDATA%\JARVIS\daemon.log`.
