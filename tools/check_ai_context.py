"""Check the portable AI context offline; no model calls or third-party packages.

Supports inline Markdown links and the single Claude import used by this repo.
Checks file targets, not anchors, remote URLs, or the meaning of instructions.
"""

from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = ("AGENTS.md", "CLAUDE.md", ".github/copilot-instructions.md")
SPEC_FILES = ("spec.md", "acceptance.md", "plan.md", "tasks.md", "evidence.md")
REQUIRED = ENTRYPOINTS + (
    "docs/ai/README.md",
    "docs/ai/PROJECT.md",
    "docs/ai/CONSTITUTION.md",
    "docs/ai/CAPABILITY-MATRIX.md",
    "docs/ai/WORKFLOW.md",
    "prompts/research-and-plan.md",
    "prompts/implement.md",
    "prompts/independent-review.md",
    "prompts/handoff.md",
    "playbooks/platform-change.md",
) + tuple("specs/_template/" + name for name in SPEC_FILES + ("handoff.md",))
SCOPES = ("docs/ai", "specs", "prompts", "playbooks")
LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\n]*)\)")


def inside(path, root):
    resolved = path.resolve()
    return resolved == root or root in resolved.parents


def link_targets(document):
    # Examples inside fenced and inline code do not import documentation.
    prose = re.sub(r"(?ms)^\x60\x60\x60[^\n]*\n.*?^\x60\x60\x60[^\n]*$", "", document)
    prose = re.sub(r"\x60[^\x60\n]+\x60", "", prose)
    return LINK.findall(prose)


def check(root=ROOT):
    root = Path(root).resolve()
    errors = []
    for relative in REQUIRED:
        path = root / relative
        if not inside(path, root):
            errors.append(f"{relative}: path leaves repository")
        elif not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"{relative}: required non-empty file missing")

    documents = {root / name for name in ENTRYPOINTS}
    for scope in SCOPES:
        directory = root / scope
        if inside(directory, root):
            documents.update(directory.rglob("*.md"))

    for path in sorted(documents):
        relative = path.relative_to(root)
        if not inside(path, root):
            errors.append(f"{relative}: path leaves repository")
            continue
        if not path.is_file():
            continue
        document = path.read_text(encoding="utf-8")
        if relative.as_posix() == "CLAUDE.md":
            imports = re.findall(r"(?m)^@([^\s]+)\s*$", document)
            if imports != ["AGENTS.md"]:
                errors.append("CLAUDE.md: expected only the @AGENTS.md import")
        targets = link_targets(document)
        if relative.as_posix() == ".github/copilot-instructions.md":
            if "../AGENTS.md" not in targets:
                errors.append(f"{relative}: missing canonical AGENTS.md link")
        for target in targets:
            parsed = urlsplit(target)
            if parsed.scheme in ("https", "http", "mailto"):
                continue
            if parsed.scheme or parsed.netloc:
                errors.append(f"{relative}: unsupported link {target}")
                continue
            if not parsed.path:
                continue  # Heading anchors are intentionally outside this check.
            destination = path.parent / unquote(parsed.path)
            if not inside(destination, root):
                errors.append(f"{relative}: link leaves repository: {target}")
            elif not destination.exists():
                errors.append(f"{relative}: missing link target: {target}")

    specs = root / "specs"
    if inside(specs, root) and specs.is_dir():
        for directory in sorted(specs.iterdir()):
            if directory.name.startswith(".") or not directory.is_dir():
                continue
            for name in SPEC_FILES:
                path = directory / name
                if not inside(path, root) or not path.is_file():
                    errors.append(f"{directory.relative_to(root)}/{name}: incomplete spec")
    return errors


def main():
    try:
        errors = check()
    except (OSError, UnicodeError, ValueError) as error:
        print(f"AI context: cannot validate: {error}", file=sys.stderr)
        return 1
    if errors:
        for error in errors:
            print(f"AI context: {error}", file=sys.stderr)
        return 1
    print("AI context: files, imports and local links OK (offline structural check).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
