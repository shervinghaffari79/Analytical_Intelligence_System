"""Persian replies: the instruction must exist and name the right script."""
from data_formulator.agents.agent_language import build_language_instruction


def test_persian_instruction_is_specific():
    for mode in ("full", "compact"):
        text = build_language_instruction("fa", mode=mode)
        assert "Persian" in text and "فارسی" in text
    assert "ی and ک" in build_language_instruction("fa")
    assert "Never remark on the language" in build_language_instruction("fa")
    assert build_language_instruction("en") == ""
