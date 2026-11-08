"""Provider translation both ways on hand-built SDK response objects. Fake clients, no network."""

from __future__ import annotations

import json
from types import SimpleNamespace

import anthropic
import httpx2
import openai
import pytest
from anthropic.types import Message
from openai.types.chat import ChatCompletion
from pydantic import BaseModel

from resolve.llm import LLMError, ToolCall, ToolSpec, assistant_message, tool_message
from resolve.llm_anthropic import AnthropicLLM
from resolve.llm_openai import OpenAILLM
from resolve.llm_schema import strict_schema
from resolve.models import TriageTicket


class NoArgs(BaseModel):
    pass


class Grant(BaseModel):
    days: int
    reason: str


TOOLS = [ToolSpec("get_profile", "The user's profile", NoArgs), ToolSpec("grant_access", "Grant days", Grant)]


class FakeCreate:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        out = self.responses.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def anthropic_client(*responses, models=()):
    create = FakeCreate(responses)
    client = SimpleNamespace(
        messages=SimpleNamespace(create=create),
        models=SimpleNamespace(list=lambda limit: [SimpleNamespace(id=m) for m in models]),
    )
    return client, create


def openai_client(*responses):
    create = FakeCreate(responses)
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))), create


def a_message(content, stop_reason):
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-x",
            "content": content,
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        }
    )


def o_completion(message, finish_reason):
    return ChatCompletion.model_validate(
        {
            "id": "c1",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-x",
            "choices": [{"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", **message}}],
        }
    )


# --- strict_schema -------------------------------------------------------------------------------


def walk(node):
    yield node
    for value in node.values():
        if isinstance(value, dict):
            yield from walk(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    yield from walk(item)


def test_strict_schema_triage_ticket():
    schema = strict_schema(TriageTicket)
    assert "$ref" not in json.dumps(schema) and "$defs" not in schema
    objects = [n for n in walk(schema) if n.get("type") == "object"]
    assert len(objects) == 4  # ticket, option, option params, bug report
    for obj in objects:
        assert obj["additionalProperties"] is False
        assert obj["required"] == list(obj["properties"])
    for key in ("minimum", "maximum", "minItems", "maxItems", "default", "title"):
        assert all(key not in n for n in walk(schema))
    assert schema["properties"]["customer_user_id"]["anyOf"] == [{"type": "string"}, {"type": "null"}]
    assert "minimum: 0" in schema["properties"]["confidence"]["description"]
    params = schema["properties"]["options"]["items"]["properties"]["params"]
    assert params["properties"]["kind"]["anyOf"][0]["enum"] == ["restore", "cancel", "refund_request"]
    TriageTicket.model_json_schema()  # the pydantic schema itself is untouched (no shared mutation)
    assert "$defs" in TriageTicket.model_json_schema()


def test_strict_schema_empty_and_free_dict():
    assert strict_schema(NoArgs) == {"properties": {}, "type": "object", "additionalProperties": False, "required": []}

    class Free(BaseModel):
        data: dict

    with pytest.raises(ValueError):
        strict_schema(Free)


# --- Anthropic -----------------------------------------------------------------------------------


def test_anthropic_plain_text_turn():
    client, create = anthropic_client(a_message([{"type": "text", "text": "Hi there"}], "end_turn"))
    llm = AnthropicLLM("sk-test", model="claude-opus-x", client=client)
    step = llm.step("sys", [{"role": "user", "content": "hello"}], [])
    assert (step.text, step.tool_calls, step.stop, step.provider) == ("Hi there", [], "end", "anthropic")
    sent = create.calls[0]
    assert sent["model"] == "claude-opus-x" and sent["system"] == "sys" and sent["max_tokens"] == 16000
    assert sent["messages"] == [{"role": "user", "content": "hello"}]
    assert sent["tools"] is anthropic.omit
    assert "thinking" not in sent and "tool_choice" not in sent


def test_anthropic_tool_round_trip_raw_replay_and_merged_results():
    content = [
        {"type": "thinking", "thinking": "check profile", "signature": "sig"},
        {"type": "tool_use", "id": "tu_1", "name": "get_profile", "input": {}},
        {"type": "tool_use", "id": "tu_2", "name": "grant_access", "input": {"days": 7, "reason": "locked"}},
    ]
    client, create = anthropic_client(a_message(content, "tool_use"), a_message([{"type": "text", "text": "Done"}], "end_turn"))
    llm = AnthropicLLM("sk-test", model="claude-opus-x", client=client)
    messages = [{"role": "user", "content": "no access"}]
    step = llm.step("sys", messages, TOOLS)
    assert step.stop == "tool" and step.text is None
    assert step.tool_calls == [ToolCall("tu_1", "get_profile", {}), ToolCall("tu_2", "grant_access", {"days": 7, "reason": "locked"})]
    tools = create.calls[0]["tools"]
    assert tools[1] == {"name": "grant_access", "description": "Grant days", "input_schema": strict_schema(Grant), "strict": True}

    messages += [assistant_message(step), tool_message(step.tool_calls[0], "{}"), tool_message(step.tool_calls[1], "blocked: not_paid", True)]
    assert llm.step("sys", messages, TOOLS).text == "Done"
    sent = create.calls[1]["messages"]
    assert len(sent) == 3
    assert sent[1] == {"role": "assistant", "content": step.raw}  # replayed unchanged, thinking block included
    assert sent[1]["content"][0].type == "thinking"
    assert sent[2] == {
        "role": "user",
        "content": [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "{}", "is_error": False},
            {"type": "tool_result", "tool_use_id": "tu_2", "content": "blocked: not_paid", "is_error": True},
        ],
    }


def test_anthropic_rebuilds_foreign_assistant_and_maps_stops():
    client, create = anthropic_client(a_message([{"type": "text", "text": "x"}], "max_tokens"), a_message([], "refusal"))
    llm = AnthropicLLM("sk-test", model="m", client=client)
    foreign = {"role": "assistant", "text": "Let me look", "tool_calls": [{"id": "c1", "name": "get_profile", "args": {}}], "raw": {"x": 1}, "provider": "openai"}
    assert llm.step("s", [{"role": "user", "content": "q"}, foreign], []).stop == "max_tokens"
    assert create.calls[0]["messages"][1] == {
        "role": "assistant",
        "content": [{"type": "text", "text": "Let me look"}, {"type": "tool_use", "id": "c1", "name": "get_profile", "input": {}}],
    }
    assert llm.step("s", [{"role": "user", "content": "q"}], []).stop == "refusal"


def test_anthropic_model_resolved_lazily_to_newest_opus():
    client, create = anthropic_client(models=["claude-sonnet-9", "claude-opus-9", "claude-opus-8"])
    assert AnthropicLLM("sk-test", client=client).model == "claude-opus-9"
    client, _ = anthropic_client(models=["claude-sonnet-9"])
    with pytest.raises(LLMError):
        AnthropicLLM("sk-test", client=client).model


def test_anthropic_api_error_becomes_llm_error_without_key():
    error = anthropic.APIConnectionError(message="boom sk-secret", request=httpx2.Request("POST", "https://api.invalid"))
    client, _ = anthropic_client(error)
    with pytest.raises(LLMError) as info:
        AnthropicLLM("sk-secret", model="m", client=client).step("s", [{"role": "user", "content": "q"}], [])
    assert "sk-secret" not in str(info.value) and "APIConnectionError" in str(info.value)


# --- OpenAI --------------------------------------------------------------------------------------


def test_openai_plain_text_turn():
    client, create = openai_client(o_completion({"content": "Hi there"}, "stop"))
    llm = OpenAILLM("sk-test", "gpt-x", client=client)
    step = llm.step("sys", [{"role": "user", "content": "hello"}], [])
    assert (step.text, step.tool_calls, step.stop, step.provider) == ("Hi there", [], "end", "openai")
    assert step.raw["content"] == "Hi there"
    sent = create.calls[0]
    assert sent["model"] == "gpt-x" and sent["max_completion_tokens"] == 16000
    assert sent["messages"] == [{"role": "system", "content": "sys"}, {"role": "user", "content": "hello"}]
    assert sent["tools"] is openai.omit and "temperature" not in sent


def test_openai_tool_round_trip():
    calls = [
        {"id": "call_1", "type": "function", "function": {"name": "get_profile", "arguments": "{}"}},
        {"id": "call_2", "type": "function", "function": {"name": "grant_access", "arguments": '{"days": 7, "reason": "locked"}'}},
    ]
    client, create = openai_client(o_completion({"content": None, "tool_calls": calls}, "tool_calls"), o_completion({"content": "Done"}, "stop"))
    llm = OpenAILLM("sk-test", "gpt-x", client=client)
    messages = [{"role": "user", "content": "no access"}]
    step = llm.step("sys", messages, TOOLS)
    assert step.stop == "tool" and step.text is None
    assert step.tool_calls == [ToolCall("call_1", "get_profile", {}), ToolCall("call_2", "grant_access", {"days": 7, "reason": "locked"})]
    assert create.calls[0]["tools"][0] == {
        "type": "function",
        "function": {"name": "get_profile", "description": "The user's profile", "parameters": strict_schema(NoArgs), "strict": True},
    }

    messages += [assistant_message(step), tool_message(step.tool_calls[0], "{}"), tool_message(step.tool_calls[1], "blocked: not_paid", True)]
    assert llm.step("sys", messages, TOOLS).text == "Done"
    sent = create.calls[1]["messages"]
    assert sent[2] == {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "get_profile", "arguments": "{}"}},
            {"id": "call_2", "type": "function", "function": {"name": "grant_access", "arguments": '{"days": 7, "reason": "locked"}'}},
        ],
    }
    assert sent[3:] == [
        {"role": "tool", "tool_call_id": "call_1", "content": "{}"},
        {"role": "tool", "tool_call_id": "call_2", "content": "blocked: not_paid"},
    ]


def test_openai_text_only_assistant_has_no_tool_calls_key():
    client, create = openai_client(o_completion({"content": "ok"}, "stop"))
    prior = {"role": "assistant", "text": "Earlier", "tool_calls": [], "raw": None, "provider": "anthropic"}
    OpenAILLM("k", "m", client=client).step("s", [{"role": "user", "content": "q"}, prior, {"role": "user", "content": "q2"}], [])
    assert create.calls[0]["messages"][2] == {"role": "assistant", "content": "Earlier"}


def test_openai_invalid_json_refusal_and_stops():
    bad = [{"id": "call_1", "type": "function", "function": {"name": "grant_access", "arguments": '{"days": 7'}}]
    client, _ = openai_client(
        o_completion({"content": None, "tool_calls": bad}, "tool_calls"),
        o_completion({"content": None, "refusal": "I can't help with that"}, "stop"),
        o_completion({"content": "cut"}, "length"),
        o_completion({"content": None}, "content_filter"),
    )
    llm = OpenAILLM("k", "m", client=client)
    q = [{"role": "user", "content": "q"}]
    assert llm.step("s", q, TOOLS).tool_calls[0].args == {"_invalid_json": '{"days": 7'}
    refusal = llm.step("s", q, [])
    assert (refusal.stop, refusal.text) == ("refusal", "I can't help with that")
    assert llm.step("s", q, []).stop == "max_tokens"
    assert llm.step("s", q, []).stop == "refusal"


def test_openai_api_error_becomes_llm_error_without_key():
    error = openai.APIConnectionError(message="boom sk-secret", request=httpx2.Request("POST", "https://api.invalid"))
    client, _ = openai_client(error)
    with pytest.raises(LLMError) as info:
        OpenAILLM("sk-secret", "m", client=client).step("s", [{"role": "user", "content": "q"}], [])
    assert "sk-secret" not in str(info.value) and "APIConnectionError" in str(info.value)
