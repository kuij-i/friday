# Friday

A self-improving personal AI assistant that lives on your machine. Friday coworks
with you, writes and runs code, helps with defensive/authorized security tasks,
remembers what matters, and answers from your iPhone over Telegram.

- **Brain:** Claude (`claude-opus-5-5`) via the Anthropic API.
- **Memory:** a local SQLite store of facts, preferences, and conversation —
  this is phase-1 self-improvement: Friday gets more useful the more you use it.
- **Tools:** files, shell, Python, local system/security inspection, and live
  web search — every side-effecting action is gated behind your approval.
- **Reach it anywhere:** a desktop CLI and a Telegram bot for your phone, with
  voice-note support (speak to Friday; transcription stays on your machine).
- **Self-coding (phase 2, scaffolded):** Friday can propose changes to its own
  code, but only onto a git branch, with tests, for you to review and merge.

## Safety model

Friday runs on *your* machine with *your* permissions, so the guardrails matter:

- **Approval gate.** Writing files, running shell commands, and executing Python
  all require approval. In the CLI you get a `y/N` prompt per action. In Telegram
  you must turn on trust (`/trust_on`); it's off by default.
- **Workspace sandbox.** File tools can only touch the `FRIDAY_WORKSPACE`
  directory. Shell/Python run with your account's permissions (that's their
  point), so only grant trust for tasks you'd run yourself.
- **Owner-locked Telegram.** Only your Telegram user id may talk to the bot.
- **Authorized security only.** Friday helps you harden, audit, and learn on
  systems you own or are authorized to test. It will decline to help attack
  systems you don't.

## Setup (Windows)

1. **Install Python 3.11+** from [python.org](https://www.python.org/downloads/)
   (check "Add Python to PATH" during install).

2. **Get the code and install dependencies** (PowerShell):

   ```powershell
   cd path\to\friday
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. **Configure.** Copy `.env.example` to `.env` and fill it in:

   ```powershell
   copy .env.example .env
   notepad .env
   ```

   At minimum set `ANTHROPIC_API_KEY` (from
   [console.anthropic.com](https://console.anthropic.com/)).

4. **Check it:**

   ```powershell
   python run.py doctor
   ```

5. **Talk to Friday in the terminal:**

   ```powershell
   python run.py cli
   ```

## Talking to Friday from your iPhone (Telegram)

1. In Telegram, message **@BotFather**, send `/newbot`, follow the prompts, and
   copy the **bot token** into `TELEGRAM_BOT_TOKEN` in `.env`.
2. Message **@userinfobot** to get your numeric user id, and put it in
   `TELEGRAM_OWNER_ID` (this locks the bot to you).
3. Start the bridge on your machine:

   ```powershell
   python run.py telegram
   ```

4. Open your bot in Telegram, send `/start`, and chat. Install the Telegram app
   on your iPhone and you can reach Friday from anywhere your machine is online.

Useful Telegram commands: `/trust_on`, `/trust_off`, `/status`, `/memory`,
`/reminders`, `/reset`.

### Reminders & recurring nudges

Ask Friday things like "remind me to stretch in 25 minutes" or "every morning
at 08:30 tell me to review my inbox". It stores the reminder and the Telegram
bridge delivers it when due (one-off or recurring). See `/reminders` to list
them. Delivery runs inside `python run.py telegram`, so keep that running (the
auto-start task below does exactly that).

### Voice notes (talk to Friday hands-free)

Send a Telegram voice message and Friday transcribes it locally (no audio
leaves your machine) and answers. This needs the optional speech-to-text extra:

```powershell
pip install -r requirements-voice.txt
```

On Windows you may also need [ffmpeg](https://www.gyan.dev/ffmpeg/builds/) on
your PATH. Pick accuracy vs. speed with `FRIDAY_WHISPER_MODEL` in `.env`
(`tiny`/`base`/`small`/`medium`/`large-v3`). Without the extra installed, voice
notes get a friendly "install this to enable voice" reply instead of failing.

## Keeping Friday running in the background (Windows)

For always-on access from your phone, register the Telegram bridge as a
scheduled task that starts at logon and restarts if it stops. From the repo
root in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install-autostart.ps1
Start-ScheduledTask -TaskName Friday    # start it now, without waiting for logon
```

Remove it later with `Unregister-ScheduledTask -TaskName Friday -Confirm:$false`.
The task runs `scripts\start-friday.ps1`, which activates your `.venv` and
launches `run.py telegram`.

## Project layout

```
friday/
  config.py            settings from .env
  memory.py            SQLite memory (facts / preferences / history)
  agent.py             Claude tool-use loop + approval gate
  prompts.py           system prompt builder
  tools/               files, shell, python, memory, security, schedule, selfcode
  transcribe.py        optional local speech-to-text for voice notes
  scheduler.py         async reminder delivery loop (runs in the Telegram bridge)
  channels/            cli.py and telegram_bot.py
  selfimprove/         phase-2 guarded self-coding (proposer)
run.py                 entrypoint: cli | telegram | doctor
scripts/               Windows auto-start (scheduled task) helpers
tests/                 pytest suite (network-free; agent uses a fake client)
```

Web search uses Claude's server-side `web_search` tool (toggle with
`FRIDAY_WEB_SEARCH`); no extra API key needed.

## Self-improvement roadmap

- **Phase 1 — memory (done).** Friday remembers facts and preferences across
  sessions and uses them automatically. Teach it with "remember that …".
- **Phase 2 — guarded self-coding (built, off by default).** Set
  `FRIDAY_SELF_CODING=true` to give Friday the `propose_self_change` tool. It
  proposes a unified diff to its own source → the change lands on a fresh
  `friday/self-improve-*` git branch → the test suite runs → you review
  `git diff` and merge by hand. It is a DANGEROUS tool (approval-gated) and
  **never merges automatically**. Keep it off until you trust it.

## Self-coding (phase 2) — enabling and using it

Phase-2 self-coding lets Friday propose changes to **its own source code**. It
is off by default and never merges anything on its own. Turn it on only once
you're comfortable reviewing diffs it writes.

### Enable

1. In your `.env`, set:

   ```ini
   FRIDAY_SELF_CODING=true
   # Only needed if you run Friday from outside the repo folder:
   # FRIDAY_REPO_ROOT=C:\path\to\friday
   ```

2. Restart Friday (`python run.py cli` or `python run.py telegram`).
3. Confirm it's live:

   ```powershell
   python run.py doctor      # should print:  self_coding: True
   ```

   The agent now has one extra tool, `propose_self_change`.

### How it works (the guardrails)

`propose_self_change` is a **DANGEROUS, approval-gated** tool, so nothing
happens silently:

1. Friday writes a unified diff against `FRIDAY_REPO_ROOT`.
2. The change is applied on a **fresh `friday/self-improve-<timestamp>`
   branch** — never your current branch.
3. The test suite runs on that branch and you get a pass/fail report.
4. **It never merges.** You review and decide:

   ```powershell
   git diff main..friday/self-improve-<timestamp>                      # review
   git checkout main && git merge friday/self-improve-<timestamp>       # accept
   git checkout main && git branch -D friday/self-improve-<timestamp>   # reject
   ```

In the CLI you'll get a `y/N` prompt before it runs; in Telegram the chat must
be in trust mode (`/trust_on`) first.

**Tip:** commit or stash your working tree before asking Friday to propose a
change, so the new branch starts from a known-clean state.

## Tests

```powershell
pip install pytest
python -m pytest -q
```
