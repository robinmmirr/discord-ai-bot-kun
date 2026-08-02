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

CONTEXT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "gather_context",
            "description": (
                "Retrieve background context before answering — this keeps your default "
                "context small and lets you pull in only what's relevant. Use scope "
                "'conversation' to search past chat messages for a TOPIC/keyword (needs a "
                "query). Use scope 'author_history' when the question is about a specific "
                "PERSON's own messages/style/catchphrases rather than a topic — e.g. 'what "
                "does X usually say' (query = that person's name). Use scope 'worldbook' to "
                "recall community history and running jokes (query optional — omit it to get "
                "everything). Use scope 'impression' to recall what you remember about the "
                "person you're currently talking to (no query needed)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "enum": ["conversation", "author_history", "worldbook", "impression"],
                        "description": "Which kind of context to retrieve.",
                    },
                    "query": {
                        "type": "string",
                        "description": (
                            "For 'conversation': the keyword/topic to search for. For "
                            "'author_history': that person's display name. Optional for "
                            "'worldbook'. Unused for 'impression'."
                        ),
                    },
                },
                "required": ["scope"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "remember_about_user",
            "description": (
                "Save a short note about the person you're currently talking to, so you "
                "can recall it in future conversations. Use this when you learn something "
                "worth remembering — a preference, an inside joke, a notable fact about them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "description": "A short note (one sentence) about this user.",
                    }
                },
                "required": ["note"],
            },
        },
    },
]


def make_tool_executor(user_id: str):
    """Builds a tool executor bound to the user in the current message, so
    'impression' and 'remember_about_user' operate on the right person without
    the model having to know or pass around a Discord user ID."""

    def execute_tool(name: str, args: dict) -> str:
        if name == "gather_context":
            scope = args.get("scope")
            query = args.get("query", "")

            if scope == "conversation":
                if not query:
                    return "A query is required for the 'conversation' scope."
                results = db.search_messages(conn, query, limit=10)
                if not results:
                    return "No matching messages found."
                return "\n".join(
                    f"[#{r['channel']} — {r['timestamp']}] {r['author']}: {r['content']}"
                    for r in results
                )

            if scope == "author_history":
                if not query:
                    return "A person's name is required for the 'author_history' scope."
                results = db.get_messages_by_author(conn, query, limit=30)
                if not results:
                    return f"No messages found from anyone matching '{query}'."
                return "\n".join(
                    f"[#{r['channel']} — {r['timestamp']}] {r['author']}: {r['content']}"
                    for r in results
                )

            if scope == "worldbook":
                if not worldbook_text:
                    return "No worldbook is configured."
                if not query:
                    return worldbook_text
                matches = [
                    line for line in worldbook_text.splitlines()
                    if query.lower() in line.lower()
                ]
                return "\n".join(matches) if matches else "No matching worldbook entries found."

            if scope == "impression":
                notes = db.get_impressions(conn, user_id)
                return "\n".join(f"- {n}" for n in notes) if notes else "No notes on this user yet."

            return f"Unknown scope: {scope}"

        if name == "remember_about_user":
            db.save_impression(conn, user_id, args["note"])
            return "Noted."

        return f"Unknown tool: {name}"

    return execute_tool


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
        tool_executor = make_tool_executor(user_id)

        try:
            async with message.channel.typing():
                reply_text, usage = backend.complete(
                    system_prompt, list(user_history), tools=CONTEXT_TOOLS, tool_executor=tool_executor
                )
        except discord.Forbidden:
            # Typing indicator requires the same permission as sending — if this channel
            # is missing it, skip the indicator but still try the LLM call and reply below,
            # so the failure surfaces on the actual reply instead of silently here.
            reply_text, usage = backend.complete(
                system_prompt, list(user_history), tools=CONTEXT_TOOLS, tool_executor=tool_executor
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
