from __future__ import annotations

from pipeline.frontmatter import dump, parse


def test_parse_extracts_front_matter_and_body():
    text = '---\ntitle: "Hi"\nstatus: draft\n---\n\nBody here.\n'
    front, body = parse(text)
    assert front["title"] == "Hi"
    assert front["status"] == "draft"
    assert body.strip() == "Body here."


def test_parse_no_front_matter_returns_text_unchanged():
    front, body = parse("just some text, no front matter")
    assert front == {}
    assert body == "just some text, no front matter"


def test_dump_roundtrip():
    front = {"title": "T", "status": "draft", "week": "2026-W27"}
    text = dump(front, "Content.")
    front2, body2 = parse(text)
    assert front2 == front
    assert body2.strip() == "Content."


def test_dump_preserves_unicode_and_list():
    front = {"title": "Zażółć — log #1", "source_initiatives": ["Collector", "Redaction"]}
    front2, _ = parse(dump(front, "body"))
    assert front2["title"] == "Zażółć — log #1"
    assert front2["source_initiatives"] == ["Collector", "Redaction"]


def test_status_flip_roundtrip():
    text = dump({"title": "T", "status": "draft"}, "body")
    front, body = parse(text)
    front["status"] = "published"
    front2, _ = parse(dump(front, body))
    assert front2["status"] == "published"
