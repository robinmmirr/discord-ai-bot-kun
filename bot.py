"""
General-purpose Discord AI companion bot.

Responds when mentioned or replied to. Persona and tone are fully config-driven
(see config.example.yaml) — no persona details are hardcoded here.

Setup:
    pip install -r requirements.txt
    cp config.example.yaml config.yaml   # then edit it
    export DISCORD_BOT_TOKEN="..."
    export DEEPSEEK_API_KEY="..."
    python bot.py
"""

import collections
import os
from pathlib import Path

import discord
import yaml
from dotenv import load_dotenv

import db
import llm

load_dotenv()

CONFIG_PATH = Path(__file__).parent / "config.yaml"

with open(CONFIG_PATH, encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

# Sort stages ascending so stage_for_score() can scan in order.
CONFIG["affection_stages"] = sorted(CONFIG["affection_stages"], key=lambda s: s["min_score"])

worldbook_text = ""
if CONFIG.get("worldbook_path"):
    worldbook_file = Path(__file__).parent / CONFIG["worldbook_path"]
    if worldbook_file.exists():
        worldbook_text = worldbook_file.read_text(encoding="utf-8")

backend = llm.get_backend(CONFIG["model"])
conn = db.get_connection()

# In-memory rolling history per user: {user_id: deque[{"role": ..., "content": ...}]}
HISTORY_TURNS = CONFIG["history_turns"]
history: dict[str, collections.deque] = collections.defaultdict(
    lambda: collections.deque(maxlen=HISTORY_TURNS * 2)
)

SEARCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_history",
            "description": (
                "Search past chat messages in this server for a keyword or phrase. "
                "Use this when asked whether someone has mentioned a specific topic, "
                "person, or thing before, or to recall something discussed earlier."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The keyword or phrase to search for.",
                    }
                },
                "required": ["query"],
            },
        },
    }
]


def execute_tool(name: str, args: dict) -> str:
    if name == "search_history":
        results = db.search_messages(conn, args["query"], limit=10)
        if not results:
            return "No matching messages found."
        return "\n".join(
            f"[#{r['channel']} — {r['timestamp']}] {r['author']}: {r['content']}" for r in results
        )
    return f"Unknown tool: {name}"


def build_system_prompt(user_id: str, user_display_name: str) -> str:
    persona = CONFIG["persona"]
    score = db.get_affection(conn, user_id)
    stage = db.stage_for_score(score, CONFIG["affection_stages"])

    parts = [
        f"You are {CONFIG['bot_name']}.",
        persona["description"].strip(),
        "Speech style:\n" + persona["speech_style"].strip(),
    ]
    if persona.get("catchphrases"):
        parts.append("Catchphrases you can use naturally (don't force them every message): "
                      + ", ".join(persona["catchphrases"]))
    if worldbook_text:
        parts.append("Community history and running jokes you can reference:\n" + worldbook_text)

    parts.append(
        f"You are talking to {user_display_name}. Their affection level with you is "
        f"{score} ({stage['name']} stage). {stage['tone_note']}"
    )
    return "\n\n".join(parts)


class CompanionBot(discord.Client):
    async def on_ready(self) -> None:
        print(f"Logged in as {self.user}")

    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        if message.content.strip():
            db.log_message(
                conn,
                message.author.display_name,
                message.content,
                getattr(message.channel, "name", "DM"),
                message.created_at.isoformat(),
            )

        if message.content.strip() == "!usage":
            stats = db.get_total_token_usage(conn)
            await message.reply(
                f"Total calls: {stats['calls']}\n"
                f"Prompt tokens: {stats['prompt_tokens']}\n"
                f"Completion tokens: {stats['completion_tokens']}\n"
                f"Total tokens: {stats['total_tokens']}",
                mention_author=False,
            )
            return

        is_mentioned = self.user in message.mentions
        # Discord auto-creates a role with the same name as the bot when it joins a
        # server, and that role often shows up above the bot's own account in the
        # @mention autocomplete — so "@BotName" very commonly resolves to a ROLE
        # mention (<@&roleID>), not a user mention. Treat mentioning any role the
        # bot itself holds in this guild as equivalent to mentioning the bot.
        is_role_mentioned = (
            message.guild is not None
            and message.guild.me is not None
            and any(role in message.role_mentions for role in message.guild.me.roles)
        )
        is_reply_to_bot = (
            message.reference is not None
            and message.reference.resolved is not None
            and message.reference.resolved.author.id == self.user.id
        )
        if not (is_mentioned or is_role_mentioned or is_reply_to_bot):
            return

        user_id = str(message.author.id)
        user_text = message.content
        for mention in message.mentions:
            if mention.id == self.user.id:
                user_text = user_text.replace(f"<@{mention.id}>", "").replace(f"<@!{mention.id}>", "")
        for role in message.guild.me.roles if message.guild and message.guild.me else []:
            user_text = user_text.replace(f"<@&{role.id}>", "")
        user_text = user_text.strip()
        if not user_text:
            return

        score = db.add_affection(
            conn,
            user_id,
            CONFIG["affection_points_per_message"],
            CONFIG["affection_daily_bonus"],
        )

        user_history = history[user_id]
        user_history.append({"role": "user", "content": user_text})

        system_prompt = build_system_prompt(user_id, message.author.display_name)

        try:
            async with message.channel.typing():
                reply_text, usage = backend.complete(
                    system_prompt, list(user_history), tools=SEARCH_TOOLS, tool_executor=execute_tool
                )
        except discord.Forbidden:
            # Typing indicator requires the same permission as sending — if this channel
            # is missing it, skip the indicator but still try the LLM call and reply below,
            # so the failure surfaces on the actual reply instead of silently here.
            reply_text, usage = backend.complete(
                system_prompt, list(user_history), tools=SEARCH_TOOLS, tool_executor=execute_tool
            )

        db.log_token_usage(
            conn, user_id, usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]
        )

        user_history.append({"role": "assistant", "content": reply_text})
        await message.reply(reply_text, mention_author=False)


if __name__ == "__main__":
    intents = discord.Intents.default()
    intents.message_content = True
    client = CompanionBot(intents=intents)
    client.run(os.environ["DISCORD_BOT_TOKEN"])
