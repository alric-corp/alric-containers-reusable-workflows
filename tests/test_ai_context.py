"""Regression checks for broken or non-portable agent context."""

from pathlib import Path
import runpy
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
CHECKER = runpy.run_path(str(ROOT / "tools/check_ai_context.py"))
check = CHECKER["check"]


class AIContextTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        for relative in CHECKER["REQUIRED"]:
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Fixture\n", encoding="utf-8")
        (self.root / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8")
        (self.root / ".github/copilot-instructions.md").write_text(
            "[Instructions](../AGENTS.md)\n", encoding="utf-8")

    def test_repository_context_is_complete_and_portable(self):
        self.assertEqual(check(ROOT), [])

    def test_complete_fixture_passes(self):
        self.assertEqual(check(self.root), [])

    def test_required_file_missing_or_empty_fails(self):
        path = self.root / "docs/ai/WORKFLOW.md"
        path.unlink()
        self.assertTrue(any("WORKFLOW.md" in error for error in check(self.root)))
        path.write_text(" \n", encoding="utf-8")
        self.assertTrue(any("WORKFLOW.md" in error for error in check(self.root)))

    def test_claude_must_import_canonical_instructions(self):
        (self.root / "CLAUDE.md").write_text("@OTHER.md\n", encoding="utf-8")
        self.assertTrue(any("@AGENTS.md" in error for error in check(self.root)))

    def test_copilot_must_link_to_canonical_instructions(self):
        (self.root / ".github/copilot-instructions.md").write_text(
            "Use instructions.\n", encoding="utf-8")
        self.assertTrue(any("canonical AGENTS.md" in error for error in check(self.root)))

    def test_missing_nested_link_fails(self):
        (self.root / "docs/ai/PROJECT.md").write_text(
            "[Missing](missing.md)\n", encoding="utf-8")
        self.assertTrue(any("missing link target" in error for error in check(self.root)))

    def test_links_outside_clone_fail(self):
        (self.root / "AGENTS.md").write_text(
            "[Sibling](../another-repo/AGENTS.md)\n", encoding="utf-8")
        self.assertTrue(any("link leaves repository" in error for error in check(self.root)))

    def test_symlink_to_external_file_fails_without_reading_it(self):
        (self.root / "docs/ai/external.md").symlink_to(self.root.parent / "outside.md")
        self.assertTrue(any("path leaves repository" in error for error in check(self.root)))

    def test_code_examples_and_remote_links_are_not_local_dependencies(self):
        (self.root / "AGENTS.md").write_text(
            "```md\n[Example](not-a-file.md)\n```\n"
            "`[Example](also-not-a-file.md)`\n"
            "[Docs](https://example.com/docs)\n[Heading](#heading)\n",
            encoding="utf-8")
        self.assertEqual(check(self.root), [])

    def test_partial_spec_cannot_pass(self):
        directory = self.root / "specs/change-one"
        directory.mkdir()
        (directory / "spec.md").write_text("# Spec\n", encoding="utf-8")
        self.assertTrue(any("change-one/acceptance.md" in error for error in check(self.root)))


if __name__ == "__main__":
    unittest.main()
