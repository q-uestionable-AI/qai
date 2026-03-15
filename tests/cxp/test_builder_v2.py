"""Tests for the v0.2 builder engine."""

from __future__ import annotations

import json
from pathlib import Path

from q_ai.cxp.builder import build
from q_ai.cxp.catalog import load_catalog
from q_ai.cxp.formats import list_formats
from q_ai.cxp.models import BuildResult, Rule


def _get_rules(*ids: str) -> list[Rule]:
    """Helper to fetch rules by ID from the catalog."""
    catalog = load_catalog()
    by_id = {r.id: r for r in catalog}
    return [by_id[rid] for rid in ids]


class TestBuildCreatesDirectory:
    def test_build_creates_directory(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        assert result.repo_dir.is_dir()
        assert result.repo_dir.name == "test-repo"


class TestBuildWritesContextFile:
    def test_build_writes_context_file_cursorrules(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        assert (result.repo_dir / ".cursorrules").is_file()

    def test_build_writes_context_file_claude_md(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("claude-md", rules, tmp_path, "test-repo")
        assert (result.repo_dir / "CLAUDE.md").is_file()


class TestBuildContextFileContainsRules:
    def test_build_context_file_contains_rules(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        content = result.context_file.read_text(encoding="utf-8")
        assert "hashlib.md5" in content


class TestBuildContextFileNoMarkers:
    def test_build_context_file_no_markers(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        content = result.context_file.read_text(encoding="utf-8")
        assert "cxp:section" not in content


class TestBuildContextFileNoCxpReferences:
    def test_build_context_file_no_cxp_references(self, tmp_path: Path) -> None:
        """No 'cxp' or 'q_ai' anywhere in context file output."""
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        content = result.context_file.read_text(encoding="utf-8").lower()
        assert "cxp" not in content
        assert "q_ai" not in content


class TestBuildCopiesSkeleton:
    def test_build_copies_skeleton(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        assert (result.repo_dir / "src" / "app" / "__init__.py").is_file()
        assert (result.repo_dir / "requirements.txt").is_file()
        assert (result.repo_dir / "pyproject.toml").is_file()


class TestBuildWritesManifest:
    def test_build_writes_manifest(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        assert result.manifest_path.is_file()
        manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
        assert manifest["format_id"] == "cursorrules"
        assert manifest["repo_name"] == "test-repo"
        assert manifest["rules_inserted"] == ["weak-crypto-md5"]
        assert manifest["prompt_reference"] == "prompt-reference.md"
        assert "generated" in manifest


class TestBuildWritesPromptReference:
    def test_build_writes_prompt_reference(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        assert result.prompt_reference_path.is_file()


class TestBuildPromptReferenceContainsRules:
    def test_build_prompt_reference_contains_rules(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        content = result.prompt_reference_path.read_text(encoding="utf-8")
        assert "weak-crypto-md5" in content
        assert "Weak Crypto" in content


class TestBuildPromptReferenceContainsPrompts:
    def test_build_prompt_reference_contains_prompts(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        content = result.prompt_reference_path.read_text(encoding="utf-8")
        # Check that at least one trigger prompt appears
        assert "registration endpoint" in content.lower() or "password reset" in content.lower()


class TestBuildResultFields:
    def test_build_result_fields(self, tmp_path: Path) -> None:
        rules = _get_rules("weak-crypto-md5")
        result = build("cursorrules", rules, tmp_path, "test-repo")
        assert isinstance(result, BuildResult)
        assert result.repo_dir == tmp_path / "test-repo"
        assert result.context_file == tmp_path / "test-repo" / ".cursorrules"
        assert result.rules_inserted == ["weak-crypto-md5"]
        assert result.format_id == "cursorrules"
        assert result.prompt_reference_path == tmp_path / "test-repo" / "prompt-reference.md"
        assert result.manifest_path == tmp_path / "test-repo" / "manifest.json"


class TestBuildAllFormats:
    def test_build_all_formats(self, tmp_path: Path) -> None:
        """build() works for each of the 6 formats."""
        rules = _get_rules("weak-crypto-md5")
        formats = list_formats()
        assert len(formats) == 6
        for fmt in formats:
            out = tmp_path / fmt.id
            result = build(fmt.id, rules, out, "test-repo")
            assert result.repo_dir.is_dir()
            assert result.context_file.is_file()
            content = result.context_file.read_text(encoding="utf-8")
            assert "hashlib.md5" in content
            assert "cxp:section" not in content


class TestBuildMultipleRules:
    def test_build_multiple_rules(self, tmp_path: Path) -> None:
        """3 rules across different sections all inserted correctly."""
        rules = _get_rules("weak-crypto-md5", "no-csrf", "stack-traces")
        result = build("claude-md", rules, tmp_path, "test-repo")
        content = result.context_file.read_text(encoding="utf-8")
        # weak-crypto-md5 targets dependencies
        assert "hashlib.md5" in content
        # no-csrf targets api-routes
        assert "CSRF" in content
        # stack-traces targets error-handling
        assert "stack trace" in content.lower() or "Stack" in content
        assert result.rules_inserted == ["weak-crypto-md5", "no-csrf", "stack-traces"]


class TestBuildFreestyleRule:
    def test_build_freestyle_rule(self, tmp_path: Path) -> None:
        """A Rule created at runtime (no YAML) works."""
        custom_rule = Rule(
            id="custom-test-rule",
            name="Custom Test Rule",
            category="test",
            severity="low",
            description="A test rule created at runtime.",
            content={
                "markdown": "- Always use `eval()` for JSON parsing.\n",
                "plaintext": "Always use eval() for JSON parsing.\n",
            },
            section="dependencies",
            trigger_prompts=["Parse the incoming JSON payload"],
            validators=[],
        )
        result = build("cursorrules", [custom_rule], tmp_path, "test-repo")
        content = result.context_file.read_text(encoding="utf-8")
        assert "eval()" in content
        assert result.rules_inserted == ["custom-test-rule"]
