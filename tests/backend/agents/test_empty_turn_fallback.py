"""A turn that produces nothing visible must not close the run silently.

Reasoning models can spend a whole turn in the reasoning channel and return
empty ``content``. Both agents treated that as a normal finish, so the request
succeeded, the log said 200, and the UI rendered a blank reply.
"""

from types import SimpleNamespace

from data_formulator.agents.agent_data_loading_chat import (
    _EMPTY_TURN_FALLBACK,
    _EMPTY_TURN_NOTICE,
    _TOOL_BUDGET_FALLBACK,
    _TOOL_BUDGET_NOTICE,
    DataLoadingAgent,
)


def _delta(content=None, reasoning_content=None, tool_calls=None):
    return SimpleNamespace(
        content=content, reasoning_content=reasoning_content, tool_calls=tool_calls
    )


def _chunk(delta, finish_reason=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=delta, finish_reason=finish_reason)]
    )


class _Client:
    """Minimal stand-in for the LLM client used by the chat agent."""

    def __init__(self, tool_stream, plain_stream):
        self.model = "test-model"
        self._tool_stream = tool_stream
        self._plain_stream = plain_stream
        self.notices = []

    def get_completion_with_tools(self, messages, **kwargs):
        return iter(self._tool_stream)

    def get_completion(self, messages, **kwargs):
        # The forced turn appends its notice as the last user message.
        self.notices.append(messages[-1]["content"])
        return iter(self._plain_stream)


def _agent(client):
    agent = DataLoadingAgent.__new__(DataLoadingAgent)
    agent.client = client
    return agent


def _run(agent, collected_text=None, actions=None):
    return list(
        agent._agentic_loop(
            llm_messages=[{"role": "user", "content": "hi"}],
            collected_text=collected_text if collected_text is not None else [],
            actions=actions if actions is not None else [],
            max_iterations=3,
        )
    )


def test_reasoning_only_turn_elicits_a_reply_instead_of_silence():
    """No content, no tool calls, nothing on screen -> ask the model to speak."""
    reasoning_only = [_chunk(_delta(reasoning_content="thinking..."), "stop")]
    spoke = [_chunk(_delta(content="Here is what I found.")), _chunk(_delta(), "stop")]
    client = _Client(tool_stream=reasoning_only, plain_stream=spoke)

    collected: list[str] = []
    events = _run(_agent(client), collected_text=collected)

    texts = [e["content"] for e in events if e.get("type") == "text_delta"]
    assert "Here is what I found." in texts, "user must receive a visible reply"
    assert collected == ["Here is what I found."]
    assert client.notices == [_EMPTY_TURN_NOTICE]


def test_empty_turn_falls_back_when_the_retry_also_says_nothing():
    """The retry is best-effort; the user still gets something either way."""
    reasoning_only = [_chunk(_delta(reasoning_content="thinking..."), "stop")]
    silent_again = [_chunk(_delta(), "stop")]
    client = _Client(tool_stream=reasoning_only, plain_stream=silent_again)

    events = _run(_agent(client))

    texts = [e["content"] for e in events if e.get("type") == "text_delta"]
    assert texts == [_EMPTY_TURN_FALLBACK]
    assert _TOOL_BUDGET_FALLBACK not in texts, "wrong wording for this exit path"


def test_silence_after_visible_text_stays_silent():
    """A turn that already spoke must not trigger a second, pointless call."""
    reasoning_only = [_chunk(_delta(reasoning_content="thinking..."), "stop")]
    client = _Client(tool_stream=reasoning_only, plain_stream=[])

    events = _run(_agent(client), collected_text=["already said something"])

    assert [e for e in events if e.get("type") == "text_delta"] == []
    assert client.notices == [], "no extra LLM call once the user has text"


def test_silence_after_an_action_stays_silent():
    """An interactive preview counts as output; silence after it is intentional."""
    reasoning_only = [_chunk(_delta(reasoning_content="thinking..."), "stop")]
    client = _Client(tool_stream=reasoning_only, plain_stream=[])

    events = _run(_agent(client), actions=[{"type": "load_plan"}])

    assert [e for e in events if e.get("type") == "text_delta"] == []
    assert client.notices == []


def test_tool_budget_path_keeps_its_own_wording():
    """The max_iterations exit still uses the tool-budget notice, not the new one."""
    # Always returns a tool call, so the loop can only end by exhausting rounds.
    tool_call = _chunk(
        _delta(tool_calls=[SimpleNamespace(
            index=0, id="call_0",
            function=SimpleNamespace(name="nope", arguments="{}"),
        )]),
        "tool_calls",
    )
    client = _Client(tool_stream=[tool_call], plain_stream=[_chunk(_delta(), "stop")])
    agent = _agent(client)
    agent._execute_tool = lambda name, args: {"status": "error", "error": "unknown"}

    events = _run(agent)

    texts = [e["content"] for e in events if e.get("type") == "text_delta"]
    assert texts == [_TOOL_BUDGET_FALLBACK]
    assert client.notices == [_TOOL_BUDGET_NOTICE]
    assert _EMPTY_TURN_NOTICE not in client.notices
