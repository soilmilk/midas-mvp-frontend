"""
Reconstructor — SPEC.md §13.

Deterministically rebuild the current complete Lean file from accepted artifacts:
  prelude lines + context + each accepted step's declarations + the latest accepted body
  (or body_initial if no step accepted yet). Logged artifacts contain no imports; the
  prelude supplies them. The final/solution.lean is built with this same rule and must
  compile independently (§14 final check).
"""
from __future__ import annotations
from typing import List


def reconstruct(prelude: List[str], context_text: str,
                accepted_declarations: List[str], body_text: str) -> str:
    parts: List[str] = []
    for line in (prelude or []):
        parts.append(line)
    parts.append("")

    ctx = (context_text or "").strip()
    if ctx:
        parts.append(ctx)
        parts.append("")

    for decls in accepted_declarations:
        d = (decls or "").strip()
        if d:
            parts.append(d)
            parts.append("")

    parts.append((body_text or "").strip())
    return "\n".join(parts).rstrip() + "\n"
