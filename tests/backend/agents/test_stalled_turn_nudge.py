"""A turn that announces a step but takes none is told to carry on.

Small local models often end a turn on "Let me inspect these tables:" without
the tool call. The user then gets an unfinished answer and no load plan.
"""

from types import SimpleNamespace

from data_formulator.agents.agent_data_loading_chat import (
    _MAX_STALL_NUDGES,
    _STALL_NOTICE,
    DataLoadingAgent,
    _looks_unfinished,
)


def _chunk(content=None, tool_calls=None, finish="stop"):
    delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


def _tool_call(name, arguments="{}"):
    return [SimpleNamespace(index=0, id="c1", function=SimpleNamespace(name=name, arguments=arguments))]


class _Client:
    """Replays one scripted model turn per call and records the prompts."""

    model = "test-model"

    def __init__(self, turns):
        self._turns = list(turns)
        self.seen = []

    def get_completion_with_tools(self, messages, **kwargs):
        self.seen.append([dict(m) for m in messages])
        return iter(self._turns.pop(0))

    def get_completion(self, messages, **kwargs):
        self.seen.append([dict(m) for m in messages])
        return iter([_chunk("fallback text")])


def _run(client, max_iterations=6):
    agent = DataLoadingAgent.__new__(DataLoadingAgent)
    agent.client = client
    agent.workspace = SimpleNamespace(prune_scratch=lambda: None)
    agent._execute_tool = lambda name, args: (
        {"actions": [{"type": "load_plan", "candidates": []}]} if name == "propose_load_plan" else {"result": "ok"}
    )
    collected, actions = [], []
    events = list(agent._agentic_loop(
        llm_messages=[{"role": "user", "content": "sales by city"}],
        collected_text=collected, actions=actions, max_iterations=max_iterations,
    ))
    return events, collected, actions


def test_looks_unfinished_is_language_independent():
    assert _looks_unfinished("Let me check the structure of these tables:", used_tools=False)
    assert _looks_unfinished("بیایید ابتدا ساختار این جداول را بررسی کنیم:", used_tools=False)
    assert _looks_unfinished("Searching more broadly…", used_tools=False)
    assert _looks_unfinished("", used_tools=True)
    # A finished answer or a question to the user is a legitimate stop.
    assert not _looks_unfinished("These are the connected sources.", used_tools=True)
    assert not _looks_unfinished("Which year do you mean?", used_tools=True)
    assert not _looks_unfinished("", used_tools=False)


def test_announced_step_without_tool_call_is_continued_to_a_load_plan():
    client = _Client([
        [_chunk("Let me inspect the tables:")],                      # stalls
        [_chunk(tool_calls=_tool_call("describe_data"), finish="tool_calls")],
        [_chunk(tool_calls=_tool_call("propose_load_plan"), finish="tool_calls")],
        [_chunk("Plan ready.")],
    ])
    events, collected, actions = _run(client)

    assert [a["type"] for a in actions] == ["load_plan"]
    # The nudge followed the model's own announcement in the next prompt.
    second = client.seen[1]
    assert second[-2] == {"role": "assistant", "content": "Let me inspect the tables:"}
    assert second[-1] == {"role": "user", "content": _STALL_NOTICE}
    assert "".join(collected).endswith("Plan ready.")


def test_silence_after_tool_calls_is_continued_not_closed():
    client = _Client([
        [_chunk(tool_calls=_tool_call("list_data"), finish="tool_calls")],
        [_chunk(None)],                                              # silent stop
        [_chunk(tool_calls=_tool_call("propose_load_plan"), finish="tool_calls")],
        [_chunk("Done.")],
    ])
    _, _, actions = _run(client)
    assert [a["type"] for a in actions] == ["load_plan"]


def test_nudges_are_bounded_and_finished_answers_are_left_alone():
    stalls = [[_chunk("Checking:")] for _ in range(_MAX_STALL_NUDGES + 1)]
    client = _Client(stalls)
    events, collected, actions = _run(client)
    assert len(client.seen) == _MAX_STALL_NUDGES + 1 and not actions

    client = _Client([[_chunk("There are 26 tables in the source.")]])
    _run(client)
    assert len(client.seen) == 1


def test_tool_results_keep_non_ascii_text_readable_for_the_model():
    client = _Client([
        [_chunk(tool_calls=_tool_call("describe_data"), finish="tool_calls")],
        [_chunk("ok.")],
    ])
    agent = DataLoadingAgent.__new__(DataLoadingAgent)
    agent.client = client
    agent.workspace = SimpleNamespace(prune_scratch=lambda: None)
    agent._execute_tool = lambda name, args: {"result": "شهر: کرج"}
    list(agent._agentic_loop([{"role": "user", "content": "x"}], [], [], 4))
    tool_msg = client.seen[1][-1]
    assert tool_msg["role"] == "tool" and "کرج" in tool_msg["content"] and "\\u" not in tool_msg["content"]
