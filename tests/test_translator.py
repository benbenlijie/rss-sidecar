import pytest
from pathlib import Path
from rss_sidecar.translator import _chunk_paragraphs, SYSTEM_PROMPT, build_glossary_section, load_glossary


class TestParagraphChunking:

    def test_short_text_single_chunk(self):
        text = "One paragraph.\n\nTwo paragraph."
        chunks = _chunk_paragraphs(text)
        assert len(chunks) == 1

    def test_long_text_split(self):
        paras = [f"Paragraph number {i}." for i in range(40)]
        text = "\n\n".join(paras)
        chunks = _chunk_paragraphs(text)
        assert len(chunks) == 3

    def test_chunk_preserves_paragraphs(self):
        paras = [f"Paragraph {i}." for i in range(30)]
        text = "\n\n".join(paras)
        chunks = _chunk_paragraphs(text, chunk_size=10)
        assert len(chunks) == 3
        rebuilt = "\n\n".join(chunks)
        result_paras = [p for p in rebuilt.split("\n\n") if p.strip()]
        assert len(result_paras) == 30


class TestSystemPrompt:

    def test_prompt_has_target_lang_placeholder(self):
        assert "{target_lang}" in SYSTEM_PROMPT

    def test_prompt_has_glossary_placeholder(self):
        assert "{glossary_section}" in SYSTEM_PROMPT

    def test_prompt_requires_paragraph_preservation(self):
        lower = SYSTEM_PROMPT.lower()
        assert "paragraph" in lower
        assert "merge" in lower or "split" in lower

    def test_prompt_keeps_proper_nouns(self):
        assert "Claude" in SYSTEM_PROMPT or "OpenAI" in SYSTEM_PROMPT


class TestGlossary:

    def test_load_existing_glossary(self, tmp_path, monkeypatch):
        yaml_content = "harness: 测试框架\nsubagent: 子智能体\n"
        gpath = tmp_path / "glossary.yaml"
        gpath.write_text(yaml_content)

        import rss_sidecar.translator as t
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(t, "_glossary_cache", None)

        result = load_glossary()
        assert "harness" in result
        assert result["harness"] == "测试框架"

    def test_no_glossary_returns_empty(self, tmp_path, monkeypatch):
        import rss_sidecar.translator as t
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(t, "_glossary_cache", None)
        result = load_glossary()
        assert result == {}

    def test_glossary_section_format(self, tmp_path, monkeypatch):
        import rss_sidecar.translator as t
        yaml_content = "harness: 测试框架\n"
        gpath = tmp_path / "glossary.yaml"
        gpath.write_text(yaml_content)
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(t, "_glossary_cache", None)

        load_glossary()
        section = build_glossary_section()
        assert "harness" in section
        assert "测试框架" in section

    def test_empty_glossary_no_section(self, tmp_path, monkeypatch):
        import rss_sidecar.translator as t
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(t, "_glossary_cache", None)
        section = build_glossary_section()
        assert section == ""
