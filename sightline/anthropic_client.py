"""Thin Anthropic Messages API client. Structured output via forced tool use —
the most broadly-supported way to guarantee schema-conformant JSON, verified
against the live API (see sightline/scoring.py)."""
from __future__ import annotations

import json
from typing import Any

import httpx

BASE_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"

# Per-MTok, verified live against docs.claude.com — see docs/... research log.
# Sonnet 5 is introductory pricing through 2026-08-31; update after that.
PRICING_USD_PER_MTOK = {
    "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    "claude-sonnet-5": {"input": 2.0, "output": 10.0},
}


class AnthropicClient:
    def __init__(self, api_key: str, client: httpx.Client | None = None) -> None:
        self._client = client or httpx.Client(
            base_url=BASE_URL.rsplit("/", 1)[0],
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            timeout=60.0,
        )

    def close(self) -> None:
        self._client.close()

    def structured_call(
        self,
        model: str,
        system: str,
        user_content: str,
        tool_name: str,
        tool_description: str,
        input_schema: dict[str, Any],
        max_tokens: int = 2000,
    ) -> tuple[dict[str, Any], float]:
        """Forces the model to respond via a single tool call matching
        `input_schema`. Returns (parsed_json, cost_usd)."""
        resp = self._client.post(
            "/messages",
            json={
                "model": model,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user_content}],
                "tools": [
                    {
                        "name": tool_name,
                        "description": tool_description,
                        "input_schema": input_schema,
                    }
                ],
                "tool_choice": {"type": "tool", "name": tool_name},
                # Extended thinking is on by default for this model and counts
                # against max_tokens — found live: a longer system prompt made
                # a chat_call spend its entire budget on invisible reasoning
                # and return empty text, stop_reason=max_tokens. We never
                # want the reasoning trace, only the final output.
                "thinking": {"type": "disabled"},
            },
        )
        resp.raise_for_status()
        data = resp.json()

        tool_use = next(b for b in data["content"] if b["type"] == "tool_use")
        usage = data["usage"]
        cost = _cost_usd(model, usage["input_tokens"], usage["output_tokens"])
        return tool_use["input"], cost

    def chat_call(
        self,
        model: str,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1500,
    ) -> tuple[str, float]:
        """Plain multi-turn conversation, no forced tool use — for the answer
        workbench, where free-form back-and-forth drafting is the point,
        not a single structured extraction."""
        resp = self._client.post(
            "/messages",
            json={
                "model": model, "max_tokens": max_tokens, "system": system, "messages": messages,
                "thinking": {"type": "disabled"},
            },
        )
        resp.raise_for_status()
        data = resp.json()

        text = "".join(b["text"] for b in data["content"] if b["type"] == "text")
        usage = data["usage"]
        cost = _cost_usd(model, usage["input_tokens"], usage["output_tokens"])
        return text, cost


def _cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    rates = PRICING_USD_PER_MTOK.get(model)
    if not rates:
        return 0.0
    return (input_tokens * rates["input"] + output_tokens * rates["output"]) / 1_000_000


def pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False)
