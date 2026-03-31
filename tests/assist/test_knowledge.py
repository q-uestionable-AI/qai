"""Tests for the assist knowledge base module."""

from __future__ import annotations

from q_ai.assist.knowledge import (
    DocumentChunk,
    _parse_frontmatter,
    _split_long_chunk,
    _split_on_headings,
    chunk_document,
    strip_mdx_jsx,
)


class TestStripMdxJsx:
    """MDX JSX stripping preserves content and removes tags."""

    def test_removes_simple_jsx_tags(self) -> None:
        text = "<Card>\nSome content\n</Card>"
        result = strip_mdx_jsx(text)
        assert "Some content" in result
        assert "<Card>" not in result
        assert "</Card>" not in result

    def test_removes_self_closing_tags(self) -> None:
        text = "<Tip />\nContent after tip"
        result = strip_mdx_jsx(text)
        assert "Content after tip" in result
        assert "<Tip" not in result

    def test_removes_tags_with_attributes(self) -> None:
        text = "<CardGroup cols={2}>\nContent\n</CardGroup>"
        result = strip_mdx_jsx(text)
        assert "Content" in result
        assert "CardGroup" not in result

    def test_preserves_non_jsx_content(self) -> None:
        text = "## Heading\n\nRegular markdown content.\n\n```python\ncode\n```"
        result = strip_mdx_jsx(text)
        assert result == text

    def test_preserves_frontmatter(self) -> None:
        text = '---\ntitle: "Test"\n---\n<Note>\nImportant\n</Note>'
        result = strip_mdx_jsx(text)
        assert "title:" in result
        assert "Important" in result
        assert "<Note>" not in result

    def test_removes_multiple_tag_types(self) -> None:
        text = "<Steps>\n<Step>\nDo thing\n</Step>\n</Steps>"
        result = strip_mdx_jsx(text)
        assert "Do thing" in result
        assert "<Steps>" not in result
        assert "<Step>" not in result

    def test_preserves_inline_html(self) -> None:
        """Inline HTML that isn't a JSX component should be preserved."""
        text = "<div>content</div>"
        result = strip_mdx_jsx(text)
        assert "<div>" in result

    def test_handles_warning_tag(self) -> None:
        text = "<Warning>\nDon't do this\n</Warning>"
        result = strip_mdx_jsx(text)
        assert "Don't do this" in result
        assert "<Warning>" not in result

    def test_handles_codegroup(self) -> None:
        text = "<CodeGroup>\n```bash\ncommand\n```\n</CodeGroup>"
        result = strip_mdx_jsx(text)
        assert "command" in result
        assert "<CodeGroup>" not in result


class TestParseFrontmatter:
    """Frontmatter extraction from markdown/MDX."""

    def test_extracts_title_and_description(self) -> None:
        text = '---\ntitle: "My Title"\ndescription: "Desc"\n---\nBody content'
        fm, body = _parse_frontmatter(text)
        assert fm["title"] == "My Title"
        assert fm["description"] == "Desc"
        assert body == "Body content"

    def test_returns_empty_without_frontmatter(self) -> None:
        text = "Just some content"
        fm, body = _parse_frontmatter(text)
        assert fm == {}
        assert body == text

    def test_handles_unquoted_values(self) -> None:
        text = "---\ntitle: Test\n---\nBody"
        fm, _body = _parse_frontmatter(text)
        assert fm["title"] == "Test"


class TestSplitOnHeadings:
    """Markdown heading-based splitting."""

    def test_splits_on_h2(self) -> None:
        text = "Intro\n\n## First\n\nContent 1\n\n## Second\n\nContent 2"
        sections = _split_on_headings(text)
        assert len(sections) >= 2

    def test_single_section_no_headings(self) -> None:
        text = "Just a paragraph without headings."
        sections = _split_on_headings(text)
        assert len(sections) == 1
        assert sections[0][1] == text

    def test_preserves_heading_in_tuple(self) -> None:
        text = "## My Heading\n\nSome content here."
        sections = _split_on_headings(text)
        headings = [h for h, _ in sections]
        assert any("My Heading" in h for h in headings)


class TestSplitLongChunk:
    """Paragraph-level splitting for oversized chunks."""

    def test_splits_on_paragraphs(self) -> None:
        # Create text that exceeds max_tokens (~4 chars per token)
        para1 = "word " * 400  # ~400 tokens (2000 chars)
        para2 = "text " * 400
        text = f"{para1}\n\n{para2}"
        chunks = _split_long_chunk(text, max_tokens=500)
        assert len(chunks) >= 2

    def test_single_paragraph_stays_intact(self) -> None:
        text = "Short paragraph."
        chunks = _split_long_chunk(text, max_tokens=1000)
        assert len(chunks) == 1


class TestChunkDocument:
    """Full document chunking pipeline."""

    def test_produces_chunks_with_metadata(self) -> None:
        text = "## Section\n\nSome content about testing."
        chunks = chunk_document(text, "test.md", "product")
        assert len(chunks) >= 1
        assert all(isinstance(c, DocumentChunk) for c in chunks)
        assert all(c.source == "test.md" for c in chunks)
        assert all(c.content_class == "product" for c in chunks)

    def test_chunk_ids_are_unique(self) -> None:
        text = "## A\n\nContent A\n\n## B\n\nContent B\n\n## C\n\nContent C"
        chunks = chunk_document(text, "test.md", "product")
        ids = [c.chunk_id for c in chunks]
        assert len(ids) == len(set(ids))

    def test_frontmatter_prepended_to_first_chunk(self) -> None:
        text = '---\ntitle: "Guide"\n---\n\n## Section\n\nContent here.'
        chunks = chunk_document(text, "guide.mdx", "product")
        assert chunks[0].text.startswith("# Guide")

    def test_user_content_class(self) -> None:
        text = "User doc content."
        chunks = chunk_document(text, "custom.md", "user")
        assert all(c.content_class == "user" for c in chunks)
