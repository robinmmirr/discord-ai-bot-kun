"""
Backfill the search database (messages_fts) with previously exported channel
history, so older conversations become searchable too — not just messages
seen since the bot's live-logging feature was added.

Usage:
    python export_history.py --channel-id <id> --output history.json
    python import_history.py --input history.json

Channel name is read from each message's "channel" field (added by
export_history.py). Pass --channel-name to override, e.g. for older export
files that predate that field.
"""

import argparse
import json

import db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument(
        "--channel-name", default=None, help="Override the channel name (default: read from the JSON)"
    )
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        messages = json.load(f)

    conn = db.get_connection()
    for msg in messages:
        channel_name = args.channel_name or msg.get("channel", "unknown")
        db.log_message(
            conn,
            msg["author"],
            msg["content"],
            channel_name,
            msg["timestamp"],
            message_id=msg.get("message_id"),
            channel_id=msg.get("channel_id"),
            guild_id=msg.get("guild_id"),
        )

    print(f"Imported {len(messages)} messages from {args.input} into the search database.")


if __name__ == "__main__":
    main()
