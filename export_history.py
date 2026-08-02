"""
Export a Discord channel's message history to a JSON file.

Setup:
    pip install discord.py

Usage:
    export DISCORD_BOT_TOKEN="your-bot-token"
    python export_history.py --channel-id 123456789012345678 --output history.json --days 90

Requires the bot to have "Read Message History" and "View Channel" permissions
on the target channel, and the "Message Content" privileged intent enabled in
the Discord Developer Portal (Bot settings page).
"""

import argparse
import asyncio
import datetime
import json
import os

import discord


async def export_channel(
    channel_id: int, output_path: str, limit: int | None, since: datetime.datetime | None
) -> None:
    token = os.environ["DISCORD_BOT_TOKEN"]

    intents = discord.Intents.default()
    intents.message_content = True

    client = discord.Client(intents=intents)
    messages: list[dict] = []

    @client.event
    async def on_ready() -> None:
        print(f"Connected as {client.user}. Fetching channel {channel_id}...")
        try:
            channel = client.get_channel(channel_id) or await client.fetch_channel(channel_id)
            print(f"Found channel: #{getattr(channel, 'name', channel_id)}. Downloading history...")
            count = 0
            async for msg in channel.history(limit=limit, after=since, oldest_first=True):
                if msg.author.bot:
                    continue
                if not msg.content.strip():
                    continue
                messages.append(
                    {
                        "author": msg.author.display_name,
                        "author_id": str(msg.author.id),
                        "content": msg.content,
                        "timestamp": msg.created_at.isoformat(),
                    }
                )
                count += 1
                if count % 200 == 0:
                    print(f"...{count} messages so far")
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
            print(f"Saved {len(messages)} messages to {output_path}")
        except discord.Forbidden as e:
            print(f"ERROR: Bot lacks permission to read this channel: {e}")
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")
        finally:
            await client.close()

    # client.start() (unlike client.run()) does not configure logging by default —
    # without this, discord.py silently swallows exceptions raised in event handlers.
    discord.utils.setup_logging()
    await client.start(token)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--channel-id", type=int, required=True)
    parser.add_argument("--output", default="history.json")
    parser.add_argument("--limit", type=int, default=None, help="Max messages to fetch (default: all)")
    parser.add_argument(
        "--days", type=int, default=None, help="Only fetch messages from the last N days (default: all time)"
    )
    args = parser.parse_args()

    since = None
    if args.days is not None:
        since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=args.days)

    asyncio.run(export_channel(args.channel_id, args.output, args.limit, since))
