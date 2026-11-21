"""The data agent must not offer tables the model made up, and must survive a
malformed tool call from a small local model."""

from types import SimpleNamespace

import pytest

from data_formulator.agents.agent_data_loading_chat import (
    _INVENTED_DATA_ERROR,
    _MALFORMED_TOOL_CALL_NOTICE,
    DataLoadingAgent,
    _CODE_READS_DATA_RE,
    _is_malformed_tool_call_error,
    _user_asked_for_synthetic,
    _values_come_from_user,
)

pytestmark = [pytest.mark.backend]

INVENTED = "state,income,households,year\nAlabama,48000,1850000,2020\nAlaska,77000,260000,2020\n"


class _Jail:
    def __init__(self, tmp_path):
        self.tmp = tmp_path

    def resolve(self, name):
        return self.tmp / name


def _agent(user_text="", images=False, synthetic=None):
    a = DataLoadingAgent.__new__(DataLoadingAgent)
    a._user_text = user_text
    a._has_images = images
    a._synthetic_ok = _user_asked_for_synthetic(user_text) if synthetic is None else synthetic
    a._literal_dfs = set()
    return a


def test_invented_inline_table_is_blocked(tmp_path):
    agent = _agent("charts of US household income please")
    out = agent._tool_show_user_data_preview({"tables": [{"name": "us_income", "data": INVENTED}]}, _Jail(tmp_path))
    assert out == {"error": _INVENTED_DATA_ERROR}
    assert not list(tmp_path.iterdir()), "nothing may be written for a blocked table"


def test_table_the_user_pasted_is_allowed(tmp_path):
    pasted = "Please load this:\nstate,income\nAlabama,48000\nAlaska,77000\n"
    agent = _agent(pasted)
    out = agent._tool_show_user_data_preview(
        {"tables": [{"name": "pasted", "data": "state,income\nAlabama,48000\nAlaska,77000\n"}]}, _Jail(tmp_path))
    assert out["actions"][0]["total_rows"] == 2 and "error" not in out["actions"][0]


def test_persian_pasted_table_is_allowed(tmp_path):
    pasted = "شهر,فروش\nتهران,900\nمشهد,800"
    agent = _agent(pasted)
    out = agent._tool_show_user_data_preview(
        {"tables": [{"name": "t", "data": "شهر,فروش\nتهران,900\nمشهد,800\n"}]}, _Jail(tmp_path))
    assert out["actions"][0]["total_rows"] == 2


def test_images_and_explicit_synthetic_requests_are_allowed(tmp_path):
    for agent in (_agent("read this screenshot", images=True), _agent("make me some synthetic sales data"),
                  _agent("داده‌های ساختگی بساز")):
        out = agent._tool_show_user_data_preview({"tables": [{"name": "x", "data": INVENTED}]}, _Jail(tmp_path))
        assert "error" not in out


def test_dataframe_typed_into_code_is_blocked_but_read_data_is_not(tmp_path):
    agent = _agent("income charts please")
    agent._literal_dfs = {"df", "top_20_series"}
    assert agent._tool_show_user_data_preview({"saved_dfs": ["top_20_series"]}, _Jail(tmp_path)) == {"error": _INVENTED_DATA_ERROR}
    # a DataFrame that came from reading real data is not in the literal set
    (tmp_path / "real.csv").write_text("a,b\n1,2\n")
    out = agent._tool_show_user_data_preview({"saved_dfs": ["real"]}, _Jail(tmp_path))
    assert out["actions"][0]["total_rows"] == 1


def test_code_that_reads_data_is_not_treated_as_typed_in():
    assert _CODE_READS_DATA_RE.search("df = pd.read_csv('scratch/x.csv')")
    assert _CODE_READS_DATA_RE.search("con = duckdb.connect()")
    assert not _CODE_READS_DATA_RE.search("df = pd.DataFrame({'title': ['Squid Game'], 'views': [835904617]})")


def test_values_check_is_whitespace_and_case_tolerant():
    import pandas as pd
    df = pd.DataFrame({"Name": ["New York"], "Pop": [8800000]})
    assert _values_come_from_user(df, "name,pop\nnew  york, 8,800,000")
    assert not _values_come_from_user(df, "show me the largest cities")


def test_synthetic_request_detection_is_specific():
    assert _user_asked_for_synthetic("generate some fake data") and _user_asked_for_synthetic("از داده‌های فرضی استفاده کن")
    assert not _user_asked_for_synthetic("show me the sample datasets") and not _user_asked_for_synthetic("درآمد خانواده‌ها")


def test_malformed_tool_call_error_detection():
    err = Exception("litellm.APIConnectionError: OllamaError: KeyError: 'message', Got unexpected response from Ollama: "
                    "{'error': 'XML syntax error on line 5: element <function> closed by </parameter>'}")
    assert _is_malformed_tool_call_error(err)
    assert not _is_malformed_tool_call_error(Exception("connection refused"))


def _chunk(content=None, finish="stop"):
    delta = SimpleNamespace(content=content, reasoning_content=None, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])


class _Client:
    model = "m"

    def __init__(self):
        self.calls = 0
        self.seen = []

    def get_completion_with_tools(self, messages, **kw):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        if self.calls == 1:
            def broken():
                yield _chunk("Let me look at the tables.")
                raise Exception("OllamaError: Got unexpected response from Ollama: {'error': 'XML syntax error on line 5'}")
            return broken()
        return iter([_chunk("Here is the answer.")])

    def get_completion(self, messages, **kw):
        return iter([_chunk("fallback")])


def test_a_malformed_tool_call_is_retried_once_with_a_notice():
    client = _Client()
    agent = DataLoadingAgent.__new__(DataLoadingAgent)
    agent.client = client
    agent.workspace = SimpleNamespace(prune_scratch=lambda: None)
    collected, actions = [], []
    events = list(agent._agentic_loop([{"role": "user", "content": "hi"}], collected, actions, 6))
    assert client.calls == 2
    assert client.seen[1][-1] == {"role": "user", "content": _MALFORMED_TOOL_CALL_NOTICE}
    assert "Here is the answer." in "".join(collected)
    assert not any(e.get("type") == "error" for e in events)


def test_analyst_delegate_is_terminal_and_detects_malformed_calls():
    from data_formulator.analyst.agent import _is_malformed_tool_call_error as analyst_check
    from data_formulator.analyst.skills.core.skill import CoreSkill

    assert analyst_check(Exception("Got unexpected response from Ollama: {'error': 'XML syntax error on line 5'}"))
    assert not analyst_check(Exception("rate limited"))

    skill = CoreSkill.__new__(CoreSkill)
    gen = skill._handle_delegate({"target": "data_loading", "delegate_prompt": "Find Netflix data"}, SimpleNamespace())
    events = []
    try:
        while True:
            events.append(next(gen))
    except StopIteration as stop:
        assert stop.value is None, "delegate must end the run (no observation for the model to narrate)"
    assert events and events[0]["type"] == "delegate"
