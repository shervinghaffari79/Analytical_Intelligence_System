"""Probe a local OpenAI-compatible model endpoint the way Data Formulator calls it.

Data Formulator streams and renders ONLY ``delta.content``. Thinking models can
put their whole answer in a separate reasoning channel, leaving ``content``
empty — the request succeeds, the agent logs "done", and the UI shows nothing.
This script makes that visible, and shows whether tool calls survive the trip.

Usage (from the repo root):

    python tools/probe_local_model.py
    python tools/probe_local_model.py --model qwen3.5:9b
    python tools/probe_local_model.py --api-base http://localhost:11434/v1
"""

import argparse
import json
import sys

TOOLS = [{
    "type": "function",
    "function": {
        "name": "propose_load_plan",
        "description": "Propose datasets to load for the user's analysis.",
        "parameters": {
            "type": "object",
            "properties": {
                "candidates": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Table names to load",
                },
                "reasoning": {"type": "string"},
            },
            "required": ["candidates", "reasoning"],
        },
    },
}]

PROMPT = "I want to analyze movie ratings by genre. Propose a load plan."


def probe(litellm, model, api_base, api_key, stream, with_tools):
    label = f"stream={stream} tools={with_tools}"
    print(f"\n{'=' * 68}\n{label}\n{'=' * 68}")

    kwargs = dict(
        model=f"openai/{model}",
        api_base=api_base,
        api_key=api_key,
        messages=[{"role": "user", "content": PROMPT}],
        stream=stream,
    )
    if with_tools:
        kwargs["tools"] = TOOLS

    try:
        resp = litellm.completion(**kwargs)
    except Exception as exc:
        print(f"  REQUEST FAILED: {type(exc).__name__}: {exc}")
        return

    if not stream:
        msg = resp.choices[0].message
        content = getattr(msg, "content", None) or ""
        reasoning = (getattr(msg, "reasoning_content", None)
                     or getattr(msg, "reasoning", None) or "")
        tool_calls = getattr(msg, "tool_calls", None) or []
        print(f"  finish_reason : {resp.choices[0].finish_reason}")
        print(f"  tool_calls    : {len(tool_calls)}")
        for tc in tool_calls:
            print(f"      -> {tc.function.name}({tc.function.arguments[:120]})")
        print(f"  content       : {len(content)} chars")
        print(f"  reasoning     : {len(reasoning)} chars")
        print("  content value : "
              + (repr(content[:300]) if content.strip()
                 else "*** EMPTY -> Data Formulator renders NOTHING ***"))
        return

    n_content = n_rc = n_reasoning = n_tool = 0
    content_parts, rc_parts = [], []
    finish = None
    # Mirror Data Formulator's accumulation (agent_data_loading_chat.py:858)
    acc = {}

    for chunk in resp:
        if not chunk.choices:
            continue
        choice = chunk.choices[0]
        delta = choice.delta
        if choice.finish_reason:
            finish = choice.finish_reason
        if getattr(delta, "content", None):
            n_content += 1
            content_parts.append(delta.content)
        # What Data Formulator accumulates (agent_utils.accumulate_reasoning_content)
        if getattr(delta, "reasoning_content", None):
            n_rc += 1
            rc_parts.append(delta.reasoning_content)
        # Non-standard key some servers emit instead — Data Formulator ignores it
        if getattr(delta, "reasoning", None):
            n_reasoning += 1
        if getattr(delta, "tool_calls", None):
            n_tool += 1
            for tcd in delta.tool_calls:
                idx = tcd.index
                slot = acc.setdefault(
                    idx, {"index": idx, "id": None, "name": "", "arguments": ""})
                if getattr(tcd, "id", None):
                    slot["id"] = tcd.id
                if getattr(tcd.function, "name", None):
                    slot["name"] = tcd.function.name
                if getattr(tcd.function, "arguments", None):
                    slot["arguments"] += tcd.function.arguments

    text = "".join(content_parts)
    print(f"  delta.content chunks          : {n_content}")
    print(f"  delta.reasoning_content chunks: {n_rc}   <- DF accumulates this")
    print(f"  delta.reasoning chunks        : {n_reasoning}   <- DF IGNORES this")
    print(f"  delta.tool_calls chunks       : {n_tool}")
    print(f"  finish_reason                 : {finish}")
    print(f"  content ({len(text)} chars)")
    print("      " + (repr(text[:300]) if text.strip()
                      else "(no visible text this turn)"))
    if rc_parts:
        joined = "".join(rc_parts)
        print(f"  reasoning_content ({len(joined)} chars, first 200):")
        print("      " + repr(joined[:200]))

    if not text.strip() and not acc:
        print("  >>> NO TEXT AND NO TOOL CALL: agent_data_loading_chat.py:876")
        print("  >>> hits `if not tool_calls_acc: return` -> UI SHOWS NOTHING")

    for slot in acc.values():
        print(f"  --- tool call (index={slot['index']!r}, id={slot['id']!r}) ---")
        print(f"      name      : {slot['name']!r}")
        print(f"      arguments : {slot['arguments'][:400]!r}")
        try:
            parsed = json.loads(slot["arguments"])
            print(f"      JSON parse: OK -> keys {sorted(parsed)}")
        except Exception as exc:
            print(f"      JSON parse: FAILED ({exc})")
            print("      >>> DF falls back to tool_args={} (line 903) -> tool runs blind")
    return acc


def probe_second_turn(litellm, model, api_base, api_key, acc):
    """Feed a tool result back and see whether the model then SAYS anything.

    This is the turn that decides whether the user sees output. If the model
    answers with neither text nor another tool call, the agent loop returns
    silently and the UI stays empty.
    """
    print(f"\n{'=' * 68}\nturn 2: after tool result (stream=True, tools=True)\n{'=' * 68}")
    if not acc:
        print("  skipped - no tool call captured in turn 1")
        return

    slot = list(acc.values())[0]
    call_id = slot["id"] or "call_0"
    messages = [
        {"role": "user", "content": PROMPT},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": call_id, "type": "function",
            "function": {"name": slot["name"], "arguments": slot["arguments"] or "{}"},
        }]},
        {"role": "tool", "tool_call_id": call_id,
         "content": '{"status":"ok","loaded":["movies"],"rows":4803,'
                    '"columns":["title","genres","vote_average"]}'},
    ]

    n_content = n_tool = 0
    parts = []
    finish = None
    try:
        for chunk in litellm.completion(
            model=f"openai/{model}", api_base=api_base, api_key=api_key,
            messages=messages, tools=TOOLS, stream=True,
        ):
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            if choice.finish_reason:
                finish = choice.finish_reason
            if getattr(choice.delta, "content", None):
                n_content += 1
                parts.append(choice.delta.content)
            if getattr(choice.delta, "tool_calls", None):
                n_tool += 1
    except Exception as exc:
        print(f"  REQUEST FAILED: {type(exc).__name__}: {exc}")
        return

    text = "".join(parts)
    print(f"  delta.content chunks    : {n_content}")
    print(f"  delta.tool_calls chunks : {n_tool}")
    print(f"  finish_reason           : {finish}")
    print(f"  content ({len(text)} chars)")
    print("      " + (repr(text[:300]) if text.strip() else "*** EMPTY ***"))
    if not text.strip() and n_tool == 0:
        print("  >>> REPRODUCED: no text, no tool call -> loop returns, UI empty")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3.5:4b")
    ap.add_argument("--api-base", default="http://localhost:11434/v1")
    ap.add_argument("--api-key", default="ollama")
    args = ap.parse_args()

    try:
        import litellm
    except ImportError:
        print("litellm not installed in this interpreter", file=sys.stderr)
        return 1

    litellm.drop_params = True
    print(f"model    : {args.model}\nendpoint : {args.api_base}")

    # Non-streaming first (matches the curl you already ran), then the
    # streaming paths the agents actually use.
    probe(litellm, args.model, args.api_base, args.api_key, stream=True, with_tools=False)
    acc = probe(litellm, args.model, args.api_base, args.api_key,
                stream=True, with_tools=True)
    probe_second_turn(litellm, args.model, args.api_base, args.api_key, acc or {})
    return 0


if __name__ == "__main__":
    sys.exit(main())
