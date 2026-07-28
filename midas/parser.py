"""Strict parsers for the reasoner action and translator transaction protocols."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

from .models import AttemptKind


_ACTION_HEADING = re.compile(
    r"(?m)^[ \t]*(NEXT STEP|PROOF|IS_FINAL_STEP|ANSWER):[ \t]*(.*)$"
)
_TRANSLATOR_HEADING = re.compile(
    r"(?m)^[ \t]*(NEW DECLARATIONS|UPDATED THEOREM BODY|"
    r"FINAL THEOREM BODY|FILLED PLACEHOLDER):[ \t]*$"
)
_SECTION_FENCE = re.compile(
    r"[ \t\r\n]*```(?:lean4)?[ \t]*\r?\n(.*?)```[ \t\r\n]*",
    re.DOTALL,
)


@dataclass
class ReasoningAction:
    ok: bool
    informal_step: str = ""
    next_step: str = ""
    proof: str = ""
    is_final_step: Optional[bool] = None
    answer: Optional[str] = None
    error: str = ""


@dataclass
class ParseResult:
    ok: bool
    declarations: Optional[str]
    body: Optional[str]
    placeholder: Optional[str] = None
    error: str = ""


def _action_error(message: str) -> ReasoningAction:
    return ReasoningAction(False, error=message)


def parse_reasoning_action(raw: str, problem_mode: str) -> ReasoningAction:
    """Parse one strict English-space action without treating malformed flags as false."""
    if problem_mode not in ("easy", "hard"):
        return _action_error(f"unsupported problem mode: {problem_mode!r}")

    matches = list(_ACTION_HEADING.finditer(raw or ""))
    by_name = {}
    for match in matches:
        by_name.setdefault(match.group(1), []).append(match)

    for name, found in by_name.items():
        if len(found) != 1:
            return _action_error(f"duplicate {name} field")

    required = ["NEXT STEP", "PROOF", "IS_FINAL_STEP"]
    for name in required:
        if name not in by_name:
            return _action_error(f"missing {name} field")

    ordered_names = [match.group(1) for match in matches]
    expected = required + (["ANSWER"] if "ANSWER" in by_name else [])
    if ordered_names != expected:
        return _action_error(
            "action fields must appear in order: NEXT STEP, PROOF, "
            "IS_FINAL_STEP, then ANSWER when permitted"
        )

    values = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        first_line = match.group(2)
        continuation = raw[match.end():end]
        values[match.group(1)] = (first_line + continuation).strip()

    if not values["NEXT STEP"]:
        return _action_error("NEXT STEP must be non-empty")
    if not values["PROOF"]:
        return _action_error("PROOF must be non-empty")
    flag = values["IS_FINAL_STEP"]
    if flag not in ("True", "False"):
        return _action_error("IS_FINAL_STEP must be exactly True or False")
    is_final = flag == "True"
    answer = values.get("ANSWER")

    if problem_mode == "easy" and answer is not None:
        return _action_error("ANSWER is forbidden in Easy Mode")
    if problem_mode == "hard" and not is_final and answer is not None:
        return _action_error("ANSWER is forbidden for a non-final Hard Mode action")
    if problem_mode == "hard" and is_final and not answer:
        return _action_error("a final Hard Mode action requires a non-empty ANSWER")

    start = by_name["NEXT STEP"][0].start()
    return ReasoningAction(
        True,
        informal_step=raw[start:].strip(),
        next_step=values["NEXT STEP"],
        proof=values["PROOF"],
        is_final_step=is_final,
        answer=answer,
    )


def parse_translator_output(
    raw: str,
    attempt_kind: AttemptKind = "exploration",
) -> ParseResult:
    """Parse exactly the section schema selected before translation."""
    schemas = {
        "exploration": ["NEW DECLARATIONS", "UPDATED THEOREM BODY"],
        "easy_finalization": ["NEW DECLARATIONS", "FINAL THEOREM BODY"],
        "hard_finalization": [
            "NEW DECLARATIONS",
            "FILLED PLACEHOLDER",
            "FINAL THEOREM BODY",
        ],
    }
    if attempt_kind not in schemas:
        return ParseResult(False, None, None, error=f"unknown attempt kind: {attempt_kind!r}")

    matches = list(_TRANSLATOR_HEADING.finditer(raw or ""))
    names = [match.group(1) for match in matches]
    expected = schemas[attempt_kind]
    for name in set(names):
        if names.count(name) > 1:
            return ParseResult(False, None, None, error=f"duplicate {name} section")
    if names != expected:
        return ParseResult(
            False,
            None,
            None,
            error=(
                f"{attempt_kind} output requires exactly these sections in order: "
                + ", ".join(expected)
            ),
        )

    sections = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        region = raw[match.end():end]
        fence = _SECTION_FENCE.fullmatch(region)
        if not fence:
            return ParseResult(
                False,
                None,
                None,
                error=(
                    f"{match.group(1)} must contain exactly one lean4 or unlabeled "
                    "code fence"
                ),
            )
        sections[match.group(1)] = fence.group(1).strip()

    declarations = sections["NEW DECLARATIONS"]
    body_heading = (
        "UPDATED THEOREM BODY"
        if attempt_kind == "exploration"
        else "FINAL THEOREM BODY"
    )
    body = sections[body_heading]
    if not body:
        return ParseResult(
            False, declarations, "", error=f"{body_heading} code block is empty"
        )
    placeholder = sections.get("FILLED PLACEHOLDER")
    if placeholder is not None and not placeholder:
        return ParseResult(
            False,
            declarations,
            body,
            placeholder="",
            error="FILLED PLACEHOLDER code block is empty",
        )
    return ParseResult(True, declarations, body, placeholder=placeholder)
