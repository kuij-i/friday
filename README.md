# Friday

A self-improving personal AI assistant that lives on your machine. Friday coworks
with you, writes and runs code, helps with defensive/authorized security tasks,
remembers what matters, and answers from your phone over Telegram.

- **Brain:** your choice of Claude (`claude-opus-5-5`, the default) via the
  Anthropic API, or an NVIDIA-hosted model (`nvidia/nemotron-3-super-120b-a12b`
  by default) via NVIDIA's API.
- **Memory:** a local SQLite store of facts, preferences, conversation history
  and reminders. This is phase-1 self-improvement: Friday gets more useful the
  more you use it.
- **Tools:** files, shell, Python, local system/security inspection, reminders
  and live web search. Every side-effecting action is gated behind your approval.
- **Reach it anywhere:** a desktop CLI and a Telegram bot for your phone, with
  voice-note support (transcription stays on your machine).
- **Self-coding (phase 2, off by default):** Friday can propose changes to its
  own code, but only onto a git branch, with tests, for you to review and merge.

## How it works

```
 you ──► CLI ──────┐                        ┌─► tools (files, shell, python,
                   ├─► Agent ─► Claude API  ┤    memory, security, reminders)
 phone ► Telegram ─┘     │   or NVIDIA API  └─► web search (Claude only)
                         ▼
                   SQLite memory ◄── reminder scheduler (inside the Telegram bridge)
```

Each message runs an agentic loop: the model replies or asks for a tool; Friday
checks the tool's risk level, asks for your approval if needed, runs it, and
feeds the result back until the model has a final answer.

## Safety model

Friday runs on *your* machine with *your* permissions, so the guardrails matter:

- **Approval gate.** Writing files, running shell commands, executing Python and
  changing memory all require approval. In the CLI you get a `y/N` prompt per
  action that always shows dangerous commands in full. In Telegram you must turn
  on trust (`/trust_on`); it's off by default. A channel with no way to ask
  you refuses these actions instead of running them.
- **Workspace sandbox.** File tools can only touch the `FRIDAY_WORKSPACE`
  directory (symlinks pointing out of it are refused). Shell/Python run with your
  account's permissions — that's their point — so only approve what you'd run
  yourself.
- **Owner-locked Telegram.** Only your Telegram user id may talk to the bot,
  and only in a private chat (group and channel messages are ignored). The
  owner comes from `TELEGRAM_OWNER_ID`, or from pairing with a one-time code
  that is shown only on your computer's terminal — nobody can claim the bot
  just by messaging it first.
- **Authorized security only.** Friday helps you harden, audit, and learn on
  systems you own or are authorized to test. It declines to help attack
  systems you don't.

## Quick start

You need **Python 3.11+** and **one** API key:

| Provider | Where to get the key | Put it in `.env` as |
| --- | --- | --- |
| Claude (Anthropic) | [console.anthropic.com](https://console.anthropic.com/) → API Keys (needs billing credit) | `ANTHROPIC_API_KEY=sk-ant-...` |
| NVIDIA | [build.nvidia.com](https://build.nvidia.com/) → sign in → "Get API Key" | `NVIDIA_API_KEY=nvapi-...` |

Friday uses whichever key you set (force one with `FRIDAY_PROVIDER`).

### Windows (PowerShell)

1. Install Python from [python.org](https://www.python.org/downloads/) and tick
   "Add Python to PATH".
2. Install Friday:

   ```powershell
   cd path\to\friday
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   copy .env.example .env
   notepad .env          # set ANTHROPIC_API_KEY or NVIDIA_API_KEY
   ```

### macOS / Linux

```bash
cd path/to/friday
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env               # set ANTHROPIC_API_KEY or NVIDIA_API_KEY
```

### Run it

```bash
python run.py doctor    # checks your configuration and prints any problems
python run.py cli       # chat with Friday in the terminal
```

`python -m friday <mode>` works too, and `pip install .` installs a `friday`
command (`friday cli`, `friday doctor`, ...).

## Using the CLI

Type a message and press Enter. Friday shows each tool it runs (`→ tool_name`)
and asks before anything with side effects. Commands:

| Command      | What it does                         |
| ------------ | ------------------------------------ |
| `/help`      | list the commands                    |
| `/memory`    | show what Friday remembers about you |
| `/reminders` | list scheduled reminders             |
| `/reset`     | clear this chat's history            |
| `/exit`      | quit (Ctrl+D / Ctrl+C work too)      |

Try: *"remember that my main project is the robotics club site"*, *"what's
listening on my ports?"*, *"write a Python script that prints the first 20
primes, save it in the workspace and run it"*.

## Talking to Friday from your phone (Telegram)

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts, and
   copy the **bot token** into `TELEGRAM_BOT_TOKEN` in `.env`.
2. Message **@userinfobot** to get your numeric user id, and put it in
   `TELEGRAM_OWNER_ID` (this locks the bot to you).
3. Start the bridge on your machine:

   ```bash
   python run.py telegram
   ```

4. Open your bot in Telegram (in a private chat), send `/start`, and chat.

**Don't know your id? Pair instead.** Leave `TELEGRAM_OWNER_ID` blank and run
`python run.py telegram`. The terminal prints a one-time code such as
`ABCD-EF23` (valid 10 minutes, single use, 5 wrong tries in total). Send
`/pair ABCD-EF23` to the bot in a private chat. Friday prints
`Paired with Telegram user <id>. Pin TELEGRAM_OWNER_ID=<id> in .env` — do that.
Until it's paired, the bot answers every message with "This bot isn't paired"
and does nothing else. `python run.py doctor` shows the owner status, and
`python run.py telegram --reset-owner` forgets a paired owner.

Upgrading from an older Friday that let the first chatter claim the bot? That
stored claim is no longer trusted. The log prints the old id: if it's yours,
pin it in `.env`; otherwise run `python run.py telegram --reset-owner`.

Telegram commands: `/help`, `/trust_on`, `/trust_off`, `/status`, `/memory`,
`/reminders`, `/reset`, `/pair <code>`.

### Reminders & recurring nudges

Ask Friday things like "remind me to stretch in 25 minutes" or "every morning
at 08:30 tell me to review my inbox". Reminders are delivered by the Telegram
bridge, so keep `python run.py telegram` running. Reminders set from the CLI
are sent to the bot's owner. Daily reminders keep their local time across
daylight-saving changes.

### Voice notes

Send a Telegram voice message and Friday transcribes it locally (no audio
leaves your machine) and answers. This needs the optional speech-to-text extra:

```bash
pip install -r requirements-voice.txt
```

On Windows you may also need [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) on
your PATH. Pick accuracy vs. speed with `FRIDAY_WHISPER_MODEL`. Without the
extra installed, voice notes get an "install this to enable voice" reply.

### Keeping Friday running in the background (Windows)

Register the Telegram bridge as a scheduled task that starts at logon and
restarts if it stops. The task runs hidden, so set `TELEGRAM_OWNER_ID` in
`.env` first (the installer refuses otherwise). From the repo root in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
Start-ScheduledTask -TaskName Friday    # start it now, without waiting for logon
```

Remove it with `Unregister-ScheduledTask -TaskName Friday -Confirm:$false`.

## Configuration

All settings live in `.env` (see `.env.example`). Relative paths are resolved
against the folder that holds `.env`, so it doesn't matter where you launch from.

| Variable | Default | Meaning |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | — | Claude key (set this **or** the NVIDIA key) |
| `NVIDIA_API_KEY` | — | NVIDIA key from build.nvidia.com |
| `FRIDAY_PROVIDER` | `auto` | `anthropic`, `nvidia`, or `auto` (whichever key is set) |
| `FRIDAY_MODEL` | provider default | `claude-opus-5-5` / `nvidia/nemotron-3-super-120b-a12b` |
| `NVIDIA_BASE_URL` | NVIDIA's hosted API | point at a self-hosted NIM instead |
| `FRIDAY_EFFORT` | `high` | Claude only: `low` / `medium` / `high` / `xhigh` / `max` |
| `TELEGRAM_BOT_TOKEN` | — | bot token from @BotFather |
| `TELEGRAM_OWNER_ID` | — | your Telegram user id (locks the bot); blank = pair with a one-time code |
| `FRIDAY_AUTO_APPROVE` | `false` | skip approval prompts (not recommended) |
| `FRIDAY_WORKSPACE` | `./workspace` | the only folder file tools may touch |
| `FRIDAY_DB` | `./data/friday.db` | memory database |
| `FRIDAY_WEB_SEARCH` | `true` | enable Claude's web search (not available on NVIDIA) |
| `FRIDAY_WEB_SEARCH_MAX_USES` | `5` | searches allowed per request |
| `FRIDAY_WHISPER_MODEL` | `base` | voice model size |
| `FRIDAY_SELF_CODING` | `false` | enable phase-2 self-coding |
| `FRIDAY_REPO_ROOT` | `.` | git repo self-coding proposes changes against |

## Using NVIDIA instead of Claude

Set `NVIDIA_API_KEY` (and leave `ANTHROPIC_API_KEY` empty, or set
`FRIDAY_PROVIDER=nvidia`). Everything else works the same: tools, approvals,
memory, reminders and Telegram. Differences:

- **Model choice matters.** Friday relies on tool calling, so use a model that
  supports it. The default `nvidia/nemotron-3-super-120b-a12b` is NVIDIA's
  agentic model. To try another (for example `moonshotai/kimi-k2.6`), set
  `FRIDAY_MODEL`; if that model can't call tools, Friday tells you so.
- **No web search** — that's a Claude server-side feature.
- **`FRIDAY_EFFORT` is ignored**, and replies are capped at 4096 tokens per
  step (Friday automatically continues longer answers).

## Self-coding (phase 2)

With `FRIDAY_SELF_CODING=true` (restart Friday, then check that `python run.py
doctor` prints `self_coding: True`), Friday gains one extra tool,
`propose_self_change`. It is **DANGEROUS and approval-gated**, and it **never
merges**:

1. Friday writes a unified diff against `FRIDAY_REPO_ROOT`.
2. The change is applied and committed on a fresh
   `friday/self-improve-<timestamp>-<id>` branch inside a temporary git
   worktree — your checkout is never switched, modified, or staged.
3. The test suite runs in that worktree and you get a pass/fail report; the
   worktree is then removed and only the branch remains.
4. You review and decide (the report prints the exact branch and base names):

   ```bash
   git diff main..friday/self-improve-<id>                  # review
   git checkout main && git merge friday/self-improve-<id>   # accept
   git branch -D friday/self-improve-<id>                    # reject
   ```

The branch starts from your last commit (`HEAD`); uncommitted changes are not
included, so commit first if the proposal should build on them.

## Project layout

```
friday/
  __main__.py          entrypoint: cli | telegram | doctor | version
  config.py            settings from .env, validated
  memory.py            SQLite memory (facts / preferences / history / reminders)
  agent.py             tool-use loop + approval gate
  providers.py         Claude client, or the NVIDIA (OpenAI-compatible) adapter
  prompts.py           system prompt builder
  tools/               files, shell, python, memory, security, schedule, selfcode
  transcribe.py        optional local speech-to-text for voice notes
  scheduler.py         async reminder delivery loop (runs in the Telegram bridge)
  channels/            cli.py and telegram_bot.py
  selfimprove/         phase-2 guarded self-coding (proposer)
run.py                 `python run.py ...` shim over friday/__main__.py
scripts/               Windows auto-start (scheduled task) helpers
tests/                 pytest suite (offline; no API key or network needed)
```

## Tests

```bash
pip install pytest ruff
python -m pytest -q     # offline: a fake client plus a localhost mock of the API
ruff check .            # lint
```

CI runs both on Linux (Python 3.11–3.13), Windows and macOS.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `config error: No API key set` | copy `.env.example` to `.env` and paste your Claude or NVIDIA key |
| "NVIDIA rejected the API key" | copy the `nvapi-...` key again from build.nvidia.com |
| "The model ... doesn't support tool calling" | set `FRIDAY_MODEL` to a tool-calling model (see above) |
| "My Claude API key was rejected" | the key is wrong or revoked — create a new one in the console |
| "I couldn't reach the Claude API" | check your internet connection / proxy |
| "Telegram rejected TELEGRAM_BOT_TOKEN" | copy the token again from @BotFather |
| The Telegram bot replies "Not authorized." | set `TELEGRAM_OWNER_ID` to *your* id (from @userinfobot) |
| The Telegram bot replies "This bot isn't paired" | set `TELEGRAM_OWNER_ID`, or send `/pair <code>` with the code from the terminal |
| The bot ignores me in a group | by design — talk to it in a private chat |
| Friday "forgot" everything | it's using a different `FRIDAY_DB`; `python run.py doctor` shows which |
| `Activate.ps1 cannot be loaded` (Windows) | run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once |
