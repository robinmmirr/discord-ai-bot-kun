"""
Backfill the search database (messages_fts) with previously exported channel
history, so older conversations become searchable too — not just messages
seen since the bot's live-logging feature was added.

Usage:
    python export_history.py --channel-id <id> --output history.json
    python import_history.py --input history.json --channel-name your-channel-name
"""

import argparse
import json

import db


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--channel-name", required=True, help="Channel name to tag these messages with")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        messages = json.load(f)

    conn = db.get_connection()
    for msg in messages:
        db.log_message(conn, msg["author"], msg["content"], args.channel_name, msg["timestamp"])

    print(f"Imported {len(messages)} messages from {args.input} into the search database.")


if __name__ == "__main__":
    main()
