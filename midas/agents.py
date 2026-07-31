"""
ReasoningAgent (§9) and TranslationAgent (§10).

Both route through OpenRouter via the OpenAI-compatible SDK (one key, two models):
  reasoning   -> openai/gpt-5            (a reasoning model: needs a LARGE max_tokens,
                                          small budgets return empty content)
  translation -> anthropic/claude-sonnet-5

Each agent supports a LIVE backend (real call) and an OFFLINE backend (canned responses,
for deterministic loop/control-flow tests without spending API calls).
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Union

from .models import AttemptKind
from .reconstructor import render_source

OPENROUTER_BASE = "https://openrouter.ai/api/v1"
OfflineResponse = Union[str, Exception, Callable[[str], str]]


def _client():
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set (source ~/.midas-mvp.env). "
                           "Live agents need it; use offline_responses for dry runs.")
    from openai import OpenAI
    # request timeout + limited retries so a slow/throttled upstream call can't stall the loop.
    # max_retries=1 keeps rate-limit backoff from stacking (2x180s instead of 3x). The loop also
    # passes a per-call `timeout` bounded by the remaining runtime budget (see loop.py).
    return OpenAI(base_url=OPENROUTER_BASE, api_key=key, timeout=120.0, max_retries=1)


@dataclass
class LLMResult:
    prompt: str
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


@dataclass
class TranslationRepairContext:
    """The immediately preceding rejected translation, supplied to a retry."""
    failed_check: str
    raw_output: str
    declarations: str
    body: str
    diagnostics: str
    attempt_kind: AttemptKind = "exploration"
    placeholder: str = ""


def _call(model: str, prompt: str, max_tokens: int, reasoning_effort: str = None,
          timeout: float = None) -> LLMResult:
    kw = dict(model=model, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens)
    if reasoning_effort:                       # only for reasoning models (gpt-5); big latency lever
        kw["reasoning_effort"] = reasoning_effort
    if timeout is not None:                    # per-call bound (loop caps by remaining runtime budget)
        kw["timeout"] = timeout
    r = _client().chat.completions.create(**kw)
    u = r.usage
    return LLMResult(prompt, (r.choices[0].message.content or "").strip(), r.model,
                     getattr(u, "prompt_tokens", 0), getattr(u, "completion_tokens", 0))


# ---------------- ReasoningAgent (§9) ----------------
class ReasoningAgent:
    def __init__(self, model: str, considerations: str,
                 offline_responses: Optional[List[OfflineResponse]] = None,
                 max_tokens: int = 32000,
                 reasoning_effort: str = "low"):
        self.model = model
        self.considerations = considerations
        self.offline = offline_responses
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort

    def build_prompt(
        self,
        informal_problem: str,
        informal_progress: str,
        *,
        problem_mode: str,
        failure_feedback: str = "",
        failed_next_step: str = "",
    ) -> str:
        if problem_mode not in ("easy", "hard"):
            raise ValueError(f"unsupported problem mode: {problem_mode!r}")
        parts = [
            (
                "You are solving a hard math problem.\n\n"
                "However, instead of generating the whole solution at once, your task is to "
                "assess the current progress and suggest the next step.\n"
            ),
            "\n## Problem statement:\n" + informal_problem,
            "\n## Current progress (Assume everything in this section has been already proved)\n" + (informal_progress or "(none yet)"),
        ]
        if failed_next_step:
            parts.append("\n## Previous step that could not be verified\n\n" +
                         failed_next_step)
        if failure_feedback:
            parts.append("\n## Feedback\n" + failure_feedback)
        
        if problem_mode == "easy":
            parts.append(
                "\n## Required action format\n\n"
                "Return exactly one action using these fields in this order:\n\n"
                "INTERMEDIATE REASONING:\n"
                "<intermediate reasoning - assess current progress, explore mathematical ideas, decide what the next step should be>\n\n"
                "NEXT STEP:\n"
                "<A statement of the next step, along with a small reason why it's true (summarized proof)>\n\n"
                "PROOF:\n"
                "<a non-empty English detailed proof of that step>\n\n"
                "IS_FINAL_STEP: True | False\n\n"
                "Use `True` exactly when this step completes the theorem; otherwise use "
                "`False`."
            )
        else:
            parts.append(
                "\n## Required action format\n\n"
                "For a non-final action, return exactly:\n\n"
                "INTERMEDIATE REASONING:\n"
                "<intermediate reasoning - assess current progress, explore mathematical ideas, decide what the next step should be>\n\n"
                "NEXT STEP:\n"
                "<A statement of the next step, along with a small reason why it's true (summarized proof)>\n\n"
                "PROOF:\n"
                "<a non-empty English detailed proof of that step>\n\n"
                "IS_FINAL_STEP: False\n\n"
                "For a final action, return exactly:\n\n"
                "INTERMEDIATE REASONING:\n"
                "<English reasoning>\n\n"
                "NEXT STEP:\n"
                "<one non-empty English proof step>\n\n"
                "PROOF:\n"
                "<a non-empty English proof of that step>\n\n"
                "IS_FINAL_STEP: True\n\n"
                "ANSWER:\n"
                "<the concrete mathematical answer in English or mathematical notation>\n\n"
                "ANSWER is required and non-empty exactly for a final action. It is "
                "forbidden for a non-final action."
            )
        parts.append("\n## Considerations\n" + self.considerations)
        return "\n".join(parts)

    def propose(self, *args, attempt_index: int = 0, timeout: float = None,
                prepared_prompt: Optional[str] = None, **kw) -> LLMResult:
        prompt = prepared_prompt if prepared_prompt is not None else self.build_prompt(*args, **kw)
        if self.offline is not None:                       # sequential canned queue
            text = self.offline.pop(0) if self.offline else ""
            if callable(text):
                text = text(prompt)
            if isinstance(text, Exception):
                raise text
            return LLMResult(prompt, text, self.model + "[offline]")
        return _call(self.model, prompt, self.max_tokens,
                     reasoning_effort=self.reasoning_effort, timeout=timeout)


# ---------------- TranslationAgent (§10) ----------------
class TranslationAgent:
    def __init__(self, model: str, considerations: str,
                 offline_responses: Optional[List[OfflineResponse]] = None,
                 max_tokens: int = 32000):
        self.model = model
        self.considerations = considerations
        self.offline = offline_responses
        self.max_tokens = max_tokens
        self.last_prompt = ""

    def build_prompt(
        self,
        header: str,
        informal_problem: str,
        prelude: List[str],
        context: str,
        accepted_decls: List[str],
        current_body: str,
        informal_candidate: str,
        compiler_feedback: str = "",
        repair_context: Optional[TranslationRepairContext] = None,
        *,
        attempt_kind: AttemptKind = "exploration",
        placeholder_header: Optional[str] = None,
        placeholder_initial_source: Optional[str] = None,
        answer: Optional[str] = None,
    ) -> str:
        if attempt_kind not in (
            "exploration", "easy_finalization", "hard_finalization"
        ):
            raise ValueError(f"unsupported attempt kind: {attempt_kind!r}")
        if attempt_kind == "hard_finalization":
            if not placeholder_header or not placeholder_initial_source or not answer:
                raise ValueError(
                    "Hard finalization requires the placeholder header, original "
                    "placeholder, and English answer"
                )
        current_file = render_source(
            prelude,
            context,
            accepted_decls,
            placeholder=placeholder_initial_source or "",
            theorem_body=(current_body or "").strip(),
        ).text

        if attempt_kind == "exploration":
            body_heading = "UPDATED THEOREM BODY"
            output_fields = (
                "NEW DECLARATIONS and complete UPDATED THEOREM BODY"
            )
            body_example = (
                f"{header}\n"
                "  <updated proof; it may contain `sorry`>"
            )
        else:
            body_heading = "FINAL THEOREM BODY"
            output_fields = (
                "NEW DECLARATIONS and complete FINAL THEOREM BODY"
                if attempt_kind == "easy_finalization"
                else (
                    "NEW DECLARATIONS, complete FILLED PLACEHOLDER, and complete "
                    "FINAL THEOREM BODY"
                )
            )
            body_example = f"{header}\n  <complete proof with no `sorry`>"

        schema = (
            "INTERMEDIATE REASONING:\n"
            "<reason about the translation>\n\n"
            "PLAN:\n"
            "<state the declarations and theorem-body changes>\n\n"
            "NEW DECLARATIONS:\n"
            "```lean4\n"
            "<complete declarations, or an empty fence>\n"
            "```\n\n"
        )
        if attempt_kind == "hard_finalization":
            schema += (
                "FILLED PLACEHOLDER:\n"
                "```lean4\n"
                f"{placeholder_header}\n"
                "  <complete definition body with no `sorry`>\n"
                "```\n\n"
            )
        schema += (
            f"{body_heading}:\n"
            "```lean4\n"
            f"{body_example}\n"
            "```"
        )

        parts = [
            (
                "You are part of a system that converts an English mathematical proof into a Lean 4 proof.\n\n"
                "The English proof is given one step at a time. You will be given the next step, and your task is to update the "
                "current Lean 4 proof state so that it reflects the step.\n\n"
                f"Return the complete {output_fields}.\n\n"
                "- Before writing Lean code, output a PLAN that states which lemmas or "
                "definitions you will propose, how you will prove them, and how you will change "
                "the theorem body to use them. If no new declaration is appropriate, say so "
                "and explain the planned theorem-body change.\n"
                "- Keep the theorem statement byte-for-byte unchanged.\n"
                "- NEW DECLARATIONS must be complete and contain no `sorry`.\n\n"
                "Follow this exact section schema:\n\n" + schema
            ),
            "\n## Problem statement in English:\n" + informal_problem,
            "\n\n## Current Lean 4 file\n```lean4\n" + current_file + "```",
            (
                "\nThe last theorem is the current theorem body. "
                "Your new lemmas and/or definitions in NEW DECLARATIONS will be added " 
                "before that theorem body. Do not repeat imports, "
                "context definitions, or already accepted declarations in NEW DECLARATIONS."
            ),
            "\n## English step to translate (with its proof)\n" + informal_candidate,
        ]
        if attempt_kind == "exploration" and placeholder_initial_source:
            parts.append(
                "\n## Hard Mode exploration constraint\n\n"
                "The unresolved placeholder shown in the current file is immutable. Do "
                "not reproduce, replace, or add a FILLED PLACEHOLDER section. New "
                "declarations are inserted before it, and the updated theorem remains "
                "after it."
            )
        elif attempt_kind == "easy_finalization":
            parts.append(
                "\n## Finalization constraint\n\n"
                "Every proposed declaration and the FINAL THEOREM BODY must be "
                "`sorry`-free."
            )
        elif attempt_kind == "hard_finalization":
            parts.append(
                "\n## Hard Mode finalization constraint\n\n"
                "The source order is: prelude, context, accepted declarations, NEW "
                "DECLARATIONS, FILLED PLACEHOLDER, FINAL THEOREM BODY. Do not place any "
                "declaration after the placeholder. Preserve this exact placeholder "
                "header byte-for-byte:\n\n"
                "```lean4\n" + placeholder_header + "\n```\n\n"
                "The reasoner's proposed concrete English answer is:\n\n"
                "<english_answer>\n" + answer.strip() + "\n</english_answer>\n\n"
                "All three output code regions must contain no `sorry`."
            )
        if repair_context is not None:
            if repair_context.attempt_kind != attempt_kind:
                raise ValueError("repair context attempt kind does not match prompt")
            parts.append(
                "\n## Previous rejected translation — repair this exact output\n\n"
                f"The `{repair_context.failed_check}` check failed. The complete previous "
                "translator response is reproduced below.\n\n"
                "### Complete previous model output\n\n"
                "<previous_model_output>\n" + repair_context.raw_output.rstrip() + "\n</previous_model_output>\n\n"
                "### All compiler errors and their source locations\n\n"
                + repair_context.diagnostics.rstrip() + "\n\n"
                "### Required repair behavior\n\n"
                "Fix every compiler error listed above. Use the reported source region and "
                "numbered excerpt to repair the exact failing expression. Preserve unrelated "
                "working code and the intended English step. Return the complete "
                f"{output_fields} again; do not return a diff or "
                "patch. Keep the original theorem header byte-for-byte unchanged."
            )
        elif compiler_feedback:
            parts.append("\n## Compiler feedback from the previous attempt (fix this)\n" + compiler_feedback)

        parts.append("\n## Considerations\n" + self.considerations)
        return "\n".join(parts)

    def translate(self, *args, attempt_index: int = 0, timeout: float = None,
                  prepared_prompt: Optional[str] = None, **kw) -> LLMResult:
        self.last_prompt = ""
        prompt = prepared_prompt if prepared_prompt is not None else self.build_prompt(*args, **kw)
        self.last_prompt = prompt
        if self.offline is not None:                       # sequential canned queue
            text = self.offline.pop(0) if self.offline else ""
            if callable(text):
                text = text(prompt)
            if isinstance(text, Exception):
                raise text
            return LLMResult(prompt, text, self.model + "[offline]")
        return _call(self.model, prompt, self.max_tokens, timeout=timeout)
