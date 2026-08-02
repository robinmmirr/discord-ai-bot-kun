"""
Turn exported Discord chat history into a short "worldbook" of memorable
jokes, running gags, and events — condensed enough to paste directly into
a system prompt.

Setup:
    pip install openai

Usage:
    export DEEPSEEK_API_KEY="your-deepseek-key"
    python extract_worldbook.py --input history.json --output worldbook.md
"""

import argparse
import json

from openai import OpenAI

BATCH_SIZE = 150  # messages per LLM call

EXTRACTION_PROMPT = """You are summarizing a Discord gaming channel's chat log into a short \
"worldbook" — a list of running jokes, memorable moments, and group-specific references that \
a chatbot could later use to sound like an in-the-know member of this community.

Rules:
- Only extract things that are genuinely reusable as a callback or joke later (running gags, \
memorable one-liners, notable events, nicknames, inside jokes).
- Skip ordinary chit-chat, logistics ("anyone up for a game?"), and anything that reads as \
private or sensitive.
- One entry per line, each a single sentence: "<short label>: <what happened / what it means>".
- If nothing in this batch is worth keeping, output nothing.

Chat log batch:
{batch_text}
"""


def format_batch(messages: list[dict]) -> str:
    return "\n".join(f"{m['author']}: {m['content']}" for m in messages)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="worldbook.md")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        messages = json.load(f)

    client = OpenAI(
        api_key=__import__("os").environ["DEEPSEEK_API_KEY"],
        base_url="https://api.deepseek.com",
    )

    entries: list[str] = []
    for i in range(0, len(messages), BATCH_SIZE):
        batch = messages[i : i + BATCH_SIZE]
        batch_text = format_batch(batch)

        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[
                {"role": "user", "content": EXTRACTION_PROMPT.format(batch_text=batch_text)},
            ],
        )
        result = response.choices[0].message.content.strip()
        if result:
            entries.append(result)
        print(f"Processed batch {i // BATCH_SIZE + 1}/{(len(messages) - 1) // BATCH_SIZE + 1}")

    with open(args.output, "w", encoding="utf-8") as f:
        f.write("# Worldbook\n\n")
        f.write("\n".join(entries))

    print(f"Wrote worldbook to {args.output} — review and edit before using it in a prompt.")


if __name__ == "__main__":
    main()
