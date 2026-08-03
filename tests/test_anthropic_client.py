import httpx
import respx

from sightline.anthropic_client import AnthropicClient, _cost_usd

BASE = "https://api.anthropic.com/v1"


def test_cost_usd_known_model():
    # Haiku 4.5: $1/$5 per MTok in/out, verified against docs.claude.com
    cost = _cost_usd("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == 6.0


def test_cost_usd_unknown_model_returns_zero():
    assert _cost_usd("some-future-model", 1000, 1000) == 0.0


@respx.mock
def test_structured_call_forces_tool_choice_and_parses_input():
    route = respx.post(f"{BASE}/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "tool_use", "name": "submit", "input": {"foo": "bar"}}],
                "usage": {"input_tokens": 100, "output_tokens": 20},
            },
        )
    )
    client = AnthropicClient(api_key="fake")
    result, cost = client.structured_call(
        model="claude-haiku-4-5",
        system="sys",
        user_content="hello",
        tool_name="submit",
        tool_description="desc",
        input_schema={"type": "object", "properties": {"foo": {"type": "string"}}},
    )
    assert result == {"foo": "bar"}
    assert cost > 0

    import json as _json

    body = _json.loads(route.calls.last.request.content)
    assert body["tool_choice"] == {"type": "tool", "name": "submit"}
    assert body["tools"][0]["name"] == "submit"
