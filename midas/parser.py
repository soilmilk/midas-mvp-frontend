"""Strict parsers for the reasoner action and translator transaction protocols."""
from __future__ import annotations
import re
from dataclasses import dataclass
from typing import Optional

from .models import AttemptKind


_ACTION_HEADING = re.compile(
    r"(?m)^[ \t]*(NEXT STEP|PROOF|STEP USEFULNESS|IS_FINAL_STEP|ANSWER|"
    r"IDEAS FOR THE FUTURE):[ \t]*(.*)$"
)
_TRANSLATOR_HEADING = re.compile(
    r"(?m)^[ \t]*(NEW DECLARATIONS|UPDATED THEOREM BODY|"
    r"FINAL THEOREM BODY|FILLED PLACEHOLDER):[ \t]*$"
)
_TRANSLATOR_PREAMBLE_HEADING = re.compile(
    r"(?m)^[ \t]*(INTERMEDIATE REASONING|PLAN):[ \t]*(.*)$"
)
_TRANSLATOR_REJECTION_HEADING = re.compile(
    r"(?m)^[ \t]*(TRANSLATION REJECTED|KIND|REASON):[ \t]*(.*)$"
)
_REVIEWER_HEADING = re.compile(
    r"(?m)^[ \t]*(INTERMEDIATE REASONING|VERDICT|FEEDBACK):[ \t]*(.*)$"
)
TRANSLATOR_REJECTION_KINDS = {
    "MATHEMATICALLY_INCORRECT",
    "MISSING_ASSUMPTION",
    "INCOMPATIBLE_WITH_CONTEXT",
}
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
    step_usefulness: str = ""
    future_ideas: str = ""
    is_final_step: Optional[bool] = None
    answer: Optional[str] = None
    error: str = ""


@dataclass
class ParseResult:
    ok: bool
    declarations: Optional[str]
    body: Optional[str]
    placeholder: Optional[str] = None
    rejected: bool = False
    rejection_kind: str = ""
    rejection_reason: str = ""
    error: str = ""


@dataclass
class SemanticReviewResult:
    ok: bool
    verdict: str = ""
    feedback: str = ""
    reasoning: str = ""
    error: str = ""


def _action_error(message: str) -> ReasoningAction:
    return ReasoningAction(False, error=message)


def _parse_translator_preamble(raw: str, expected: list[str]) -> str:
    """Validate the reasoning that must precede a translation decision."""
    matches = list(_TRANSLATOR_PREAMBLE_HEADING.finditer(raw or ""))
    names = [match.group(1) for match in matches]
    if names != expected:
        return "translator output must begin with non-empty " + " and ".join(expected)
    if raw[:matches[0].start()].strip():
        return f"{expected[0]} must be the first translator section"
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        value = (match.group(2) + raw[match.end():end]).strip()
        if not value:
            return f"{match.group(1)} must be non-empty"
    return ""


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

    required = [
        "NEXT STEP", "PROOF", "STEP USEFULNESS", "IS_FINAL_STEP",
        "IDEAS FOR THE FUTURE",
    ]
    for name in required:
        if name not in by_name:
            return _action_error(f"missing {name} field")

    ordered_names = [match.group(1) for match in matches]
    expected = ["NEXT STEP", "PROOF", "STEP USEFULNESS", "IS_FINAL_STEP"]
    if "ANSWER" in by_name:
        expected.append("ANSWER")
    expected.append("IDEAS FOR THE FUTURE")
    if ordered_names != expected:
        return _action_error(
            "action fields must appear in order: NEXT STEP, PROOF, STEP "
            "USEFULNESS, IS_FINAL_STEP, ANSWER when permitted, then IDEAS "
            "FOR THE FUTURE"
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
    usefulness_lines = values["STEP USEFULNESS"].splitlines()
    usefulness = usefulness_lines[0].strip() if usefulness_lines else ""
    if usefulness not in ("High", "Medium", "Low"):
        return _action_error("STEP USEFULNESS must start with exactly High, Medium, or Low")
    if not values["IDEAS FOR THE FUTURE"]:
        return _action_error("IDEAS FOR THE FUTURE must be non-empty")
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

    return ReasoningAction(
        True,
        informal_step=(
            f"NEXT STEP:\n{values['NEXT STEP']}\n\n"
            f"PROOF:\n{values['PROOF']}"
        ),
        next_step=values["NEXT STEP"],
        proof=values["PROOF"],
        step_usefulness=usefulness,
        future_ideas=values["IDEAS FOR THE FUTURE"],
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

    rejection_matches = list(_TRANSLATOR_REJECTION_HEADING.finditer(raw or ""))
    if rejection_matches:
        preamble_error = _parse_translator_preamble(
            raw[:rejection_matches[0].start()], ["INTERMEDIATE REASONING"]
        )
        if preamble_error:
            return ParseResult(False, None, None, error=preamble_error)
        rejection_names = [match.group(1) for match in rejection_matches]
        if rejection_names != ["TRANSLATION REJECTED", "KIND", "REASON"]:
            return ParseResult(
                False, None, None,
                error=("a translator rejection requires exactly TRANSLATION REJECTED, "
                       "KIND, and REASON in that order"),
            )
        if list(_TRANSLATOR_HEADING.finditer(raw or "")):
            return ParseResult(
                False, None, None,
                error="a translator rejection must not include Lean transaction sections",
            )
        rejection_values = {}
        for index, match in enumerate(rejection_matches):
            end = (rejection_matches[index + 1].start()
                   if index + 1 < len(rejection_matches) else len(raw))
            rejection_values[match.group(1)] = (
                match.group(2) + raw[match.end():end]
            ).strip()
        if rejection_values["TRANSLATION REJECTED"]:
            return ParseResult(
                False, None, None,
                error="TRANSLATION REJECTED heading must not contain a value",
            )
        rejection_kind = rejection_values["KIND"]
        if rejection_kind not in TRANSLATOR_REJECTION_KINDS:
            return ParseResult(
                False, None, None,
                error=("KIND must be exactly one of: "
                       + ", ".join(sorted(TRANSLATOR_REJECTION_KINDS))),
            )
        rejection_reason = rejection_values["REASON"]
        if not rejection_reason:
            return ParseResult(
                False, None, None,
                error="REASON must be a non-empty English explanation",
            )
        return ParseResult(
            True, None, None, rejected=True,
            rejection_kind=rejection_kind,
            rejection_reason=rejection_reason,
        )

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

    preamble_error = _parse_translator_preamble(
        raw[:matches[0].start()], ["INTERMEDIATE REASONING", "PLAN"]
    )
    if preamble_error:
        return ParseResult(False, None, None, error=preamble_error)

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


def parse_semantic_review(raw: str) -> SemanticReviewResult:
    """Parse the strict semantic-review gate response."""
    matches = list(_REVIEWER_HEADING.finditer(raw or ""))
    names = [match.group(1) for match in matches]
    expected = ["INTERMEDIATE REASONING", "VERDICT", "FEEDBACK"]
    if names != expected:
        return SemanticReviewResult(
            False,
            error=("reviewer output requires exactly these sections in order: "
                   + ", ".join(expected)),
        )
    if raw[:matches[0].start()].strip():
        return SemanticReviewResult(
            False, error="INTERMEDIATE REASONING must be the first reviewer section"
        )
    values = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        values[match.group(1)] = (
            match.group(2) + raw[match.end():end]
        ).strip()
    if not values["INTERMEDIATE REASONING"]:
        return SemanticReviewResult(False, error="reviewer reasoning must be non-empty")
    if values["VERDICT"] not in ("ALIGNED", "MISALIGNED"):
        return SemanticReviewResult(
            False, error="VERDICT must be exactly ALIGNED or MISALIGNED"
        )
    if not values["FEEDBACK"]:
        return SemanticReviewResult(False, error="FEEDBACK must be non-empty")
    return SemanticReviewResult(
        True,
        verdict=values["VERDICT"],
        feedback=values["FEEDBACK"],
        reasoning=values["INTERMEDIATE REASONING"],
    )
