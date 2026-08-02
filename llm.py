"""Model backend abstraction. Currently supports DeepSeek (OpenAI-compatible API).

To add another provider (e.g. Claude), add a branch in `get_client()` and a matching
`complete()` implementation — the rest of the bot only calls `complete()`.
"""

import json
import os

from openai import OpenAI

MAX_TOOL_ITERATIONS = 3


class DeepSeekBackend:
    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        self.client = OpenAI(
            api_key=os.environ["DEEPSEEK_API_KEY"],
            base_url="https://api.deepseek.com",
        )

    def complete(
        self,
        system_prompt: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor=None,
    ) -> tuple[str, dict]:
        conversation = [{"role": "system", "content": system_prompt}] + list(messages)
        total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

        for _ in range(MAX_TOOL_ITERATIONS):
            kwargs = {"model": self.model_id, "messages": conversation}
            if tools:
                kwargs["tools"] = tools
                kwargs["tool_choice"] = "auto"

            response = self.client.chat.completions.create(**kwargs)
            usage = response.usage
            total_usage["prompt_tokens"] += usage.prompt_tokens
            total_usage["completion_tokens"] += usage.completion_tokens
            total_usage["total_tokens"] += usage.total_tokens

            message = response.choices[0].message

            if message.tool_calls and tool_executor:
                conversation.append(
                    {
                        "role": "assistant",
                        "content": message.content,
                        "tool_calls": [tc.model_dump() for tc in message.tool_calls],
                    }
                )
                for tool_call in message.tool_calls:
                    args = json.loads(tool_call.function.arguments)
                    result = tool_executor(tool_call.function.name, args)
                    conversation.append(
                        {
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": result,
                        }
                    )
                continue  # feed tool results back for a final answer

            return message.content, total_usage

        # Hit MAX_TOOL_ITERATIONS without a final answer — return whatever text we have.
        return message.content or "", total_usage


def get_backend(model_config: dict):
    provider = model_config["provider"]
    if provider == "deepseek":
        return DeepSeekBackend(model_config["model_id"])
    raise ValueError(f"Unknown model provider: {provider}")
