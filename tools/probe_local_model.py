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

    text = "".join(content_parts)
    print(f"  delta.content chunks          : {n_content}")
    print(f"  delta.reasoning_content chunks: {n_rc}   <- DF accumulates this")
    print(f"  delta.reasoning chunks        : {n_reasoning}   <- DF IGNORES this")
    print(f"  delta.tool_calls chunks       : {n_tool}")
    print(f"  finish_reason                 : {finish}")
    print(f"  content ({len(text)} chars)")
    print("      " + (repr(text[:300]) if text.strip()
                      else "*** EMPTY -> Data Formulator renders NOTHING ***"))
    if rc_parts:
        joined = "".join(rc_parts)
        print(f"  reasoning_content ({len(joined)} chars, first 200):")
        print("      " + repr(joined[:200]))


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
    probe(litellm, args.model, args.api_base, args.api_key, stream=False, with_tools=False)
    probe(litellm, args.model, args.api_base, args.api_key, stream=True, with_tools=False)
    probe(litellm, args.model, args.api_base, args.api_key, stream=True, with_tools=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
