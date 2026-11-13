"""Streamed tool calls must stay separate calls with valid JSON arguments."""

import json
from types import SimpleNamespace

from data_formulator.agents.agent_utils import ToolCallAccumulator


def _delta(index=0, id=None, name=None, arguments=None):
    return SimpleNamespace(index=index, id=id, function=SimpleNamespace(name=name, arguments=arguments))


def test_openai_style_fragments_are_joined_per_index():
    acc = ToolCallAccumulator()
    acc.add(_delta(0, "a", "find_data", '{"que'))
    acc.add(_delta(0, None, None, 'ry": "x"}'))
    acc.add(_delta(1, "b", "list_data", "{}"))
    calls = acc.calls()
    assert [(c["id"], c["name"], json.loads(c["arguments"])) for c in calls] == [
        ("a", "find_data", {"query": "x"}), ("b", "list_data", {})]


def test_ollama_parallel_calls_sharing_index_zero_stay_separate():
    # Ollama via LiteLLM: each call arrives whole, every one with index 0.
    acc = ToolCallAccumulator()
    acc.add(_delta(0, "call_1", "describe_data", '{"table_key": "db.tbl_faktor"}'))
    acc.add(_delta(0, "call_2", "describe_data", '{"table_key": "db.tbl_moshtari"}'))
    acc.add(_delta(0, "call_3", "describe_data", '{"table_key": "db.tbl_shahr"}'))
    calls = acc.calls()
    assert [c["id"] for c in calls] == ["call_1", "call_2", "call_3"]
    assert [json.loads(c["arguments"])["table_key"] for c in calls] == [
        "db.tbl_faktor", "db.tbl_moshtari", "db.tbl_shahr"]


def test_concatenated_objects_without_ids_are_split():
    acc = ToolCallAccumulator()
    acc.add(_delta(0, None, "describe_data", '{"table_key": "a"}'))
    acc.add(_delta(0, None, None, '{"table_key": "b"}'))
    calls = acc.calls()
    assert [json.loads(c["arguments"])["table_key"] for c in calls] == ["a", "b"]
    assert len({c["id"] for c in calls}) == 2


def test_unparseable_arguments_become_empty_object_and_are_flagged():
    acc = ToolCallAccumulator()
    acc.add(_delta(0, "x", "probe_data", '{"query": '))
    (call,) = acc.calls()
    # History must always re-serialise: never keep invalid JSON.
    assert call["arguments"] == "{}" and call["malformed"] is True


def test_non_ascii_arguments_survive():
    acc = ToolCallAccumulator()
    acc.add(_delta(0, "x", "find_data", '{"query": "فروش|شهر"}'))
    assert json.loads(acc.calls()[0]["arguments"]) == {"query": "فروش|شهر"}
    assert not ToolCallAccumulator()
