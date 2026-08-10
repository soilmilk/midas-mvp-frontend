"""
Reconstructor — SPEC.md §13.

Deterministically rebuild the current complete Lean file from accepted artifacts:
  prelude lines + context + each accepted step's declarations + the latest accepted body
  (or body_initial if no step accepted yet). Logged artifacts contain no imports; the
  prelude supplies them. The final/solution.lean is built with this same rule and must
  compile independently (§14 final check).
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class SourceRegion:
    name: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class RenderedSource:
    text: str
    regions: List[SourceRegion]


def render_source(prelude: List[str], context: str,
                  accepted_declarations: List[str],
                  candidate_declarations: str = "",
                  placeholder: str = "",
                  theorem_body: str = "") -> RenderedSource:
    """Render exact Lean source and 1-based, inclusive region spans.

    The leading blank line when ``prelude`` is empty intentionally preserves
    the historical output of ``reconstruct``.
    """
    pieces = []
    prelude_text = "\n".join(prelude or [])
    if prelude_text:
        pieces.append(("prelude", prelude_text))

    context_text = (context or "").strip()
    if context_text:
        pieces.append(("context", context_text))

    for declarations in accepted_declarations or []:
        text = (declarations or "").strip()
        if text:
            pieces.append(("accepted_declarations", text))

    candidate_text = (candidate_declarations or "").strip()
    if candidate_text:
        pieces.append(("candidate_declarations", candidate_text))

    placeholder_text = (placeholder or "").strip()
    if placeholder_text:
        pieces.append(("placeholder", placeholder_text))

    theorem_text = (theorem_body or "").strip()
    if theorem_text:
        pieces.append(("theorem_body", theorem_text))

    text = "" if prelude_text else "\n"
    regions: List[SourceRegion] = []
    for index, (name, piece) in enumerate(pieces):
        if index:
            text += "\n\n"
        start_line = text.count("\n") + 1
        text += piece
        regions.append(SourceRegion(
            name=name,
            start_line=start_line,
            end_line=start_line + piece.count("\n"),
        ))

    return RenderedSource(text=text.rstrip() + "\n", regions=regions)


def reconstruct(prelude: List[str], context_text: str,
                accepted_declarations: List[str], body_text: str) -> str:
    return render_source(
        prelude, context_text, accepted_declarations, theorem_body=body_text
    ).text


def reconstruct_hard(prelude: List[str], context_text: str,
                     accepted_declarations: List[str],
                     placeholder_text: str, body_text: str) -> str:
    return render_source(
        prelude,
        context_text,
        accepted_declarations,
        placeholder=placeholder_text,
        theorem_body=body_text,
    ).text
