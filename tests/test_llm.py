import llm


def test_extract_json_plain_fenced_and_wrapped():
    assert llm._extract_json('{"a": 1}') == {"a": 1}
    assert llm._extract_json('```json\n{"a": 2}\n```') == {"a": 2}
    assert llm._extract_json('Sure! Here you go: {"a": 3} hope that helps') == {"a": 3}


def test_extract_json_rejects_garbage():
    import pytest
    with pytest.raises(llm.LLMError):
        llm._extract_json("no json here")


def test_generate_json_falls_through_backends(monkeypatch):
    calls = []

    def bad(system, user):
        calls.append("claude"); raise llm.LLMError("limit")

    def good(system, user):
        calls.append("ollama"); return {"ok": True}

    monkeypatch.setattr(llm, "ORDER", ["claude", "ollama"])
    monkeypatch.setitem(llm._BACKENDS, "claude", bad)
    monkeypatch.setitem(llm._BACKENDS, "ollama", good)
    assert llm.generate_json("s", "u") == ({"ok": True}, "ollama")
    assert calls == ["claude", "ollama"]


def test_generate_json_raises_when_all_fail(monkeypatch):
    import pytest
    monkeypatch.setattr(llm, "ORDER", [])
    with pytest.raises(llm.LLMError):
        llm.generate_json("s", "u")
