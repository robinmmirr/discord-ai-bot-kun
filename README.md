# discord-ai-bot-kun

A general-purpose, config-driven AI companion bot for Discord. No hardcoded
persona, no economy system — just a personality you define in YAML, an
affection system that shapes tone over time, and an optional "worldbook" of
community in-jokes.

## Features

- Responds when @mentioned or replied to (handles both real user mentions and
  Discord's auto-created bot role mention — see `bot.py` for why both matter)
- Persona fully defined in `config.yaml` — swap it out for a different bot with zero code changes
- Per-user affection score (SQLite) that shifts tone from "stranger" to "close friend" over time
- Live keyword search over server chat history (SQLite FTS5) — the model can call a
  `search_history` tool to answer "did anyone mention X" style questions
- Token usage tracking — `!usage` reports cumulative prompt/completion/total tokens
- Optional worldbook: paste in community in-jokes/history extracted from your own chat logs
- Pluggable model backend (`llm.py`) — ships with DeepSeek, easy to add others

## Code vs. data — what this repo actually shares

This repo is the **tool**, not your server's data. Cloning it gives you the bot's
logic only — no persona, no chat history, no API keys, no affection scores.
Everything user- or server-specific is generated locally when *you* run it, and
is excluded via `.gitignore`:

| Excluded from git | What it is |
|---|---|
| `.env` | Your Discord bot token + DeepSeek API key |
| `config.yaml` | Your actual persona (only `config.example.yaml`, a placeholder, is tracked) |
| `bot.db` | Affection scores, token usage log, and the searchable message index |
| `*history*.json` | Any chat history you export with `export_history.py` |

If you also want to backfill searchable history from your own server (see
below), that's a script **you** run with **your own** bot token — it pulls
messages into a local `bot.db` on your machine. Nothing about another user's
server, and nothing produced by these scripts, is ever bundled into this repo
or shared by cloning it.

## Setup

```bash
pip install -r requirements.txt
cp config.example.yaml config.yaml   # edit persona, tone, affection stages
export DISCORD_BOT_TOKEN="..."
export DEEPSEEK_API_KEY="..."
python bot.py
```

Requires the "Message Content" privileged intent enabled for your bot in the
Discord Developer Portal, and "Read Message History" + "View Channel" +
"Send Messages" permissions in the server.

## Building a worldbook from your own chat history (optional)

Two helper scripts:

1. `export_history.py` — pulls a channel's message history into a JSON file
   (`--days N` limits it to the last N days instead of the whole channel)
2. `extract_worldbook.py` — summarizes that JSON into short worldbook entries via DeepSeek

```bash
python export_history.py --channel-id <id> --output history.json --days 90
python extract_worldbook.py --input history.json --output worldbook.md
```

Review `worldbook.md` by hand before using it — drop anything private or not
actually worth keeping. Point `worldbook_path` in `config.yaml` at the file.

## Backfilling searchable history (optional)

Once the bot is running, every new message it sees gets logged automatically —
no extra step needed. But messages sent *before* the bot went live aren't in
there yet. To make older conversations searchable too, export a channel (as
above) and import it into the search index:

```bash
python export_history.py --channel-id <id> --output history.json --days 90
python import_history.py --input history.json --channel-name your-channel-name
```

This is a one-time backfill per channel — do it once for each channel you want
searchable, and the bot keeps itself up to date from then on.

## Adding another model provider

Add a class to `llm.py` implementing `complete(system_prompt, messages) -> str`,
then add a branch for it in `get_backend()`.
