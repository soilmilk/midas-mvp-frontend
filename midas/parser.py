"""
OutputParser — SPEC.md §11.

Extract the two required Lean code blocks from the translator's raw output:
  NEW DECLARATIONS      -> declarations.lean  (a declaration delta; may be empty)
  UPDATED THEOREM BODY  -> body.lean          (full theorem declaration)
Intermediate reasoning / prose outside the required sections is ignored.
Import lines are stripped from parsed artifacts (§12).
If either required section is missing / unparseable -> format_failed.
"""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

# a fenced code block after a heading: ```[lang]\n ... \n```
_FENCE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)


@dataclass
class ParseResult:
    ok: bool
    declarations: Optional[str]   # "" is valid (empty delta); None means section absent
    body: Optional[str]
    error: str = ""


def _strip_imports(code: str) -> str:
    return "\n".join(ln for ln in code.splitlines() if not ln.strip().startswith("import ")).strip()


def _section_after(text: str, *headings) -> Optional[str]:
    """Return the first fenced code block that appears after any of `headings`."""
    for h in headings:
        # match the heading as a line (case-insensitive), optional trailing colon
        m = re.search(rf"(?im)^\s*{re.escape(h)}\s*:?\s*$", text)
        if not m:
            continue
        fence = _FENCE.search(text, m.end())
        if fence:
            return fence.group(1)
    return None


def parse_translator_output(raw: str) -> ParseResult:
    decls = _section_after(raw, "NEW DECLARATIONS")
    body = _section_after(raw, "UPDATED THEOREM BODY")

    if decls is None:
        return ParseResult(False, None, None, "missing or unparseable NEW DECLARATIONS section")
    if body is None:
        return ParseResult(False, None, None, "missing or unparseable UPDATED THEOREM BODY section")

    decls = _strip_imports(decls)          # may be "" (empty delta is allowed, §7)
    body = _strip_imports(body)
    if not body.strip():
        return ParseResult(False, decls, "", "UPDATED THEOREM BODY code block is empty")
    return ParseResult(True, decls, body)
