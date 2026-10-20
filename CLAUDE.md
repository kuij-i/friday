# Friday — developer notes

Friday is a personal AI assistant: a Claude-powered agent with persistent
memory, a gated tool belt, a CLI, and a Telegram bridge (with voice + a
reminder scheduler). This file orients future work.

## Architecture

- `friday/__main__.py` — entrypoint (`cli|telegram|doctor|version`); `run.py`
  and the `friday` console script are thin shims over it.
- `friday/config.py` — all settings from env/`.env` (`Config`), validated.
  Relative paths resolve against the `.env` folder (`PROJECT_DIR`), not the cwd.
- `friday/memory.py` — thread-safe SQLite store: facts, preferences, messages,
  reminders. One connection + `RLock`; WAL; input size caps.
- `friday/agent.py` — the agentic tool-use loop. Injectable `client` for tests.
  Never raises to the caller: API/tool failures become readable text. Handles
  `refusal`, `pause_turn`, `max_tokens` continuation, and an iteration cap.
- `friday/providers.py` — `make_client(config)`: the Anthropic client, or
  `NvidiaClient`, which mimics `client.messages.create` and translates to
  NVIDIA's OpenAI-compatible chat API (raises `ProviderError` with user text).
- `friday/prompts.py` — system prompt (base policy + memory context + channel
  note). Channels map to a kind via `_channel_kind`: `cli`, or `tg:<chat_id>`
  (and legacy `telegram`) → Telegram.
- `friday/tools/` — each module exposes `make_*_tools(...)` returning `Tool`s
  with a `Risk` level; `build_registry(config, memory, channel)` assembles them.
  - `base.py` risk model + registry + `Approver` type.
  - `files.py` (workspace-sandboxed), `shell.py` (shell/python, DANGEROUS),
    `memory_tools.py`, `security.py` (local defensive), `schedule.py`,
    `selfcode.py` (gated by `FRIDAY_SELF_CODING`).
- `friday/channels/` — `cli.py` (interactive y/N approval) and
  `telegram_bot.py` (owner-locked, `/pair` pairing mode, `/trust` gate, voice,
  scheduler lifecycle). `pairing.py` resolves the owner (no `telegram` import).
- `friday/scheduler.py` — async reminder delivery loop (runs in the bot).
- `friday/selfimprove/proposer.py` — phase-2 self-coding: stages a diff on a
  branch, runs tests, never merges.

## Safety invariants (do not regress)

1. Tools with `Risk >= LOW` pass through the `Approver` unless `auto_approve`.
   An Agent created without an approver **denies** them (fail closed);
   `FRIDAY_AUTO_APPROVE` is the only switch that skips the gate.
2. File tools never resolve outside `config.workspace`.
3. Telegram is owner-locked. The owner comes only from `TELEGRAM_OWNER_ID` or
   a local `/pair` code (`channels/pairing.py`: printed to the terminal only,
   10 min, single use, 5 wrong tries in total) — never from an implicit
   claim, and a legacy claimed id is never honoured. Only private chats with
   the owner are served; unauthorised users get a fixed reply that reveals
   nothing. Side-effecting tools need `/trust_on`.
4. `agent.send()` must never raise — always returns a string and logs it.
5. Self-coding never merges automatically; it only stages + tests on a branch.

## Conventions

- Python 3.11+, `from __future__ import annotations` in every module.
- Keep handlers returning `ToolResult(content, is_error=...)`; never raise out
  of a tool — the agent surfaces errors as tool results.
- Default models live in `config.DEFAULT_MODELS` (`claude-opus-5-5` for
  anthropic, `nvidia/nemotron-3-super-120b-a12b` for nvidia); do not hardcode
  model strings elsewhere.

## Dev commands

```bash
pip install -r requirements.txt && pip install pytest ruff
python -m pytest -q          # tests (network-free; agent uses a fake client)
ruff check .                 # lint
python run.py doctor         # config check
```

Tests must stay offline: use `tests/fakes.FakeClient` for the agent, or the
localhost mock in `tests/test_sdk_roundtrip.py` to exercise the real SDK.
CI also runs on Windows and macOS — mark POSIX-shell tests with `posix_only`.
