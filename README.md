# Friday

A self-improving personal AI assistant that lives on your machine. Friday coworks
with you, writes and runs code, helps with defensive/authorized security tasks,
remembers what matters, and answers from your phone over Telegram.

- **Brain:** Claude (`claude-opus-5-5`) via the Anthropic API, with adaptive
  thinking and a configurable effort level.
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
 you ──► CLI ──────┐                       ┌─► tools (files, shell, python,
                   ├─► Agent ─► Claude API ┤    memory, security, reminders)
 phone ► Telegram ─┘     │  (tool-use loop) └─► web search (server-side)
                         ▼
                   SQLite memory ◄── reminder scheduler (inside the Telegram bridge)
```

Each message runs an agentic loop: Claude replies or asks for a tool; Friday
checks the tool's risk level, asks for your approval if needed, runs it, and
feeds the result back until Claude has a final answer.

## Safety model

Friday runs on *your* machine with *your* permissions, so the guardrails matter:

- **Approval gate.** Writing files, running shell commands, executing Python and
  changing memory all require approval. In the CLI you get a `y/N` prompt per
  action that always shows dangerous commands in full. In Telegram you must turn
  on trust (`/trust_on`); it's off by default.
- **Workspace sandbox.** File tools can only touch the `FRIDAY_WORKSPACE`
  directory (symlinks pointing out of it are refused). Shell/Python run with your
  account's permissions — that's their point — so only approve what you'd run
  yourself.
- **Owner-locked Telegram.** Only your Telegram user id may talk to the bot.
- **Authorized security only.** Friday helps you harden, audit, and learn on
  systems you own or are authorized to test. It declines to help attack
  systems you don't.

## Quick start

You need **Python 3.11+** and an **Anthropic API key** from
[console.anthropic.com](https://console.anthropic.com/).

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
   notepad .env          # set ANTHROPIC_API_KEY
   ```

### macOS / Linux

```bash
cd path/to/friday
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env               # set ANTHROPIC_API_KEY
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

4. Open your bot in Telegram, send `/start`, and chat.

Telegram commands: `/help`, `/trust_on`, `/trust_off`, `/status`, `/memory`,
`/reminders`, `/reset`.

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
restarts if it stops. From the repo root in PowerShell:

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
| `ANTHROPIC_API_KEY` | — (required) | your Anthropic API key |
| `FRIDAY_MODEL` | `claude-opus-5-5` | Claude model |
| `FRIDAY_EFFORT` | `high` | `low` / `medium` / `high` / `xhigh` / `max` |
| `TELEGRAM_BOT_TOKEN` | — | bot token from @BotFather |
| `TELEGRAM_OWNER_ID` | — | your Telegram user id (locks the bot) |
| `FRIDAY_AUTO_APPROVE` | `false` | skip approval prompts (not recommended) |
| `FRIDAY_WORKSPACE` | `./workspace` | the only folder file tools may touch |
| `FRIDAY_DB` | `./data/friday.db` | memory database |
| `FRIDAY_WEB_SEARCH` | `true` | enable Claude's web search |
| `FRIDAY_WEB_SEARCH_MAX_USES` | `5` | searches allowed per request |
| `FRIDAY_WHISPER_MODEL` | `base` | voice model size |
| `FRIDAY_SELF_CODING` | `false` | enable phase-2 self-coding |
| `FRIDAY_REPO_ROOT` | `.` | git repo self-coding proposes changes against |

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
  agent.py             Claude tool-use loop + approval gate
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
| `config error: ANTHROPIC_API_KEY is not set` | copy `.env.example` to `.env` and paste your key |
| "My Claude API key was rejected" | the key is wrong or revoked — create a new one in the console |
| "I couldn't reach the Claude API" | check your internet connection / proxy |
| "Telegram rejected TELEGRAM_BOT_TOKEN" | copy the token again from @BotFather |
| The Telegram bot replies "Not authorized." | set `TELEGRAM_OWNER_ID` to *your* id (from @userinfobot) |
| Friday "forgot" everything | it's using a different `FRIDAY_DB`; `python run.py doctor` shows which |
| `Activate.ps1 cannot be loaded` (Windows) | run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once |
