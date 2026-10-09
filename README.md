# Friday

A self-improving personal AI assistant that lives on your machine. Friday coworks
with you, writes and runs code, helps with defensive/authorized security tasks,
remembers what matters, and answers from your iPhone over Telegram.

- **Brain:** Claude (`claude-opus-5-5`) via the Anthropic API.
- **Memory:** a local SQLite store of facts, preferences, and conversation —
  this is phase-1 self-improvement: Friday gets more useful the more you use it.
- **Tools:** files, shell, Python, local system/security inspection — every
  side-effecting action is gated behind your approval.
- **Reach it anywhere:** a desktop CLI and a Telegram bot for your phone.
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

Useful Telegram commands: `/trust_on`, `/trust_off`, `/status`, `/memory`, `/reset`.

## Keeping Friday running in the background (Windows)

For always-on access from your phone, run the Telegram bridge via **Task
Scheduler**: create a task that runs at logon, action
`...\friday\.venv\Scripts\python.exe` with argument `run.py telegram` and "Start
in" set to the friday folder. (A proper Windows service via NSSM also works.)

## Project layout

```
friday/
  config.py            settings from .env
  memory.py            SQLite memory (facts / preferences / history)
  agent.py             Claude tool-use loop + approval gate
  prompts.py           system prompt builder
  tools/               files, shell, python, memory, security tools
  channels/            cli.py and telegram_bot.py
  selfimprove/         phase-2 guarded self-coding scaffold
run.py                 entrypoint: cli | telegram | doctor
tests/                 pytest suite
```

## Self-improvement roadmap

- **Phase 1 — memory (done).** Friday remembers facts and preferences across
  sessions and uses them automatically. Teach it with "remember that …".
- **Phase 2 — guarded self-coding (scaffolded, off by default).** See
  `friday/selfimprove/proposer.py`. The intended loop: Friday proposes a diff to
  its own source → it lands on a `friday/self-improve-*` git branch → tests run →
  you review `git diff` and merge by hand. Wire it in only once you trust it.

## Tests

```powershell
pip install pytest
python -m pytest -q
```
