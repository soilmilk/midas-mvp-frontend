"""
StructureChecker — SPEC.md §8 (byte-exact theorem header) and §12 (structure checks).

Runs on the parsed declarations.lean / body.lean BEFORE compilation. Any violation ->
the attempt is format_failed and must not reach the compiler (§18). Each violation is
recorded so compile.json.structure_check can distinguish parse vs structure failures
(NOTES §3 judgment call: single format_failed status, sub-cause logged here).
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field
from typing import List

BY_MARKER = ":= by"


class HeaderError(ValueError):
    """Raised when body_initial.lean has no `:= by` — fail loudly, don't mis-parse (user tweak #3)."""


def extract_header(body_initial: str) -> str:
    """
    Theorem header = from the theorem/lemma keyword through the first `:= by` (inclusive),
    stored byte-exact (newlines preserved). Defensive: if `:= by` is absent, fail loudly.
    """
    idx = body_initial.find(BY_MARKER)
    if idx == -1:
        raise HeaderError(
            "body_initial.lean does not contain ':= by' — cannot extract the theorem header. "
            "The target theorem must be written in tactic mode ending in ':= by'. "
            f"Got:\n{body_initial.strip()[:400]}")
    start = re.search(r"(?m)^\s*(theorem|lemma)\b", body_initial)
    begin = start.start() if start else 0
    # trim only leading whitespace of the located declaration, keep internal bytes exact
    header = body_initial[begin: idx + len(BY_MARKER)]
    return header.lstrip("\n").rstrip() if start else header[: idx + len(BY_MARKER)]


_DECL_NAME = re.compile(r"(?m)^\s*(?:theorem|lemma|def|abbrev|instance)\s+([A-Za-z_][A-Za-z0-9_.']*)")
_THM_DECL = re.compile(r"(?m)^\s*theorem\s+[A-Za-z_]")
_SORRY = re.compile(r"\bsorry\b")


def declared_names(code: str) -> List[str]:
    return _DECL_NAME.findall(code or "")


@dataclass
class StructureResult:
    ok: bool
    violations: List[str] = field(default_factory=list)


def check_structure(declarations: str, body: str, header: str,
                    previous_accepted_names: List[str]) -> StructureResult:
    v: List[str] = []

    # §12: declarations must not contain sorry
    if _SORRY.search(declarations or ""):
        v.append("declarations contain `sorry`")

    # §12: declarations must not repeat previously accepted names
    prev = set(previous_accepted_names)
    for name in declared_names(declarations):
        if name in prev:
            v.append(f"declaration repeats previously accepted name: {name}")

    # §12: body must contain exactly one theorem declaration
    n_thm = len(_THM_DECL.findall(body or ""))
    if n_thm != 1:
        v.append(f"body must contain exactly one theorem declaration, found {n_thm}")

    # §8: body must begin byte-for-byte with the stored header (tolerate only leading blank space)
    if not body.lstrip().startswith(header.lstrip()):
        v.append("body does not begin with the exact original theorem header")

    return StructureResult(ok=(len(v) == 0), violations=v)


def body_contains_sorry(body: str) -> bool:
    return _SORRY.search(body or "") is not None
