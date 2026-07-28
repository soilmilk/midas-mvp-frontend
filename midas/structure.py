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
_PLACEHOLDER_DEF = re.compile(
    r"(?m)^[ \t]*def[ \t]+(?P<name>[A-Za-z_][A-Za-z0-9_.']*)\b")
_TOP_LEVEL_DECL = re.compile(
    r"(?m)^[ \t]*(?:theorem|lemma|def|abbrev|instance|example|opaque|axiom|"
    r"inductive|structure|class)\b")
_FORBIDDEN_COMMAND = re.compile(
    r"(?m)^[ \t]*(?:import|namespace|end|section|open|export|variable|"
    r"set_option|attribute|local|scoped|syntax|macro|elab|universe|"
    r"mutual|include|omit|private|protected|noncomputable|notation|"
    r"infix|infixl|infixr|prefix|postfix|initialize|#\w+)\b")


def declared_names(code: str) -> List[str]:
    return _DECL_NAME.findall(code or "")


@dataclass
class StructureResult:
    ok: bool
    violations: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlaceholderInfo:
    header: str
    name: str
    source: str


class PlaceholderError(ValueError):
    """The placeholder does not match the supported one-definition shape."""


def _mask_comments(source: str) -> str:
    """Replace Lean comments with spaces while retaining exact line positions."""
    out = list(source)
    i = 0
    block_depth = 0
    in_string = False
    while i < len(source):
        if block_depth:
            if source.startswith("/-", i):
                out[i:i + 2] = "  "
                block_depth += 1
                i += 2
            elif source.startswith("-/", i):
                out[i:i + 2] = "  "
                block_depth -= 1
                i += 2
            else:
                if source[i] != "\n":
                    out[i] = " "
                i += 1
            continue
        if in_string:
            if source[i] == "\\" and i + 1 < len(source):
                out[i:i + 2] = "  "
                i += 2
            else:
                if source[i] == '"':
                    in_string = False
                if source[i] != "\n":
                    out[i] = " "
                i += 1
            continue
        if source.startswith("--", i):
            while i < len(source) and source[i] != "\n":
                out[i] = " "
                i += 1
        elif source.startswith("/-", i):
            out[i:i + 2] = "  "
            block_depth = 1
            i += 2
        elif source[i] == '"':
            out[i] = " "
            in_string = True
            i += 1
        else:
            i += 1
    return "".join(out)


def _placeholder_info(source: str, require_sorry: bool) -> PlaceholderInfo:
    original = source or ""
    masked = _mask_comments(original)
    forbidden = _FORBIDDEN_COMMAND.search(masked)
    if forbidden:
        command = forbidden.group(0).strip().split()[0]
        raise PlaceholderError(f"placeholder contains forbidden top-level command: {command}")

    declarations = list(_TOP_LEVEL_DECL.finditer(masked))
    definitions = list(_PLACEHOLDER_DEF.finditer(masked))
    if len(declarations) != 1 or len(definitions) != 1:
        raise PlaceholderError("placeholder must contain exactly one top-level `def`")

    definition = definitions[0]
    marker = masked.find(BY_MARKER, definition.end())
    if marker < 0:
        raise PlaceholderError("placeholder definition must use tactic mode ending in `:= by`")
    if masked[:definition.start()].strip():
        raise PlaceholderError("placeholder contains unrelated code before its definition")

    name = definition.group("name")
    header = original[definition.start():marker + len(BY_MARKER)].rstrip()
    has_sorry = _SORRY.search(masked) is not None
    if require_sorry and not has_sorry:
        raise PlaceholderError("initial placeholder must contain at least one `sorry`")
    if not require_sorry and has_sorry:
        raise PlaceholderError("filled placeholder must not contain `sorry`")
    return PlaceholderInfo(header=header, name=name, source=original)


def extract_placeholder_info(source: str) -> PlaceholderInfo:
    return _placeholder_info(source, require_sorry=True)


def check_filled_placeholder(source: str, expected_header: str,
                             expected_name: str) -> StructureResult:
    violations: List[str] = []
    try:
        info = _placeholder_info(source, require_sorry=False)
    except PlaceholderError as error:
        return StructureResult(False, [str(error)])
    if info.header != expected_header:
        violations.append("filled placeholder does not begin with the exact original header")
    if info.name != expected_name:
        violations.append(
            f"filled placeholder name changed from {expected_name!r} to {info.name!r}")
    return StructureResult(not violations, violations)


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
