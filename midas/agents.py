"""
ReasoningAgent (§9), TranslationAgent (§10), and the semantic reviewer.

All route through OpenRouter via the OpenAI-compatible SDK (one key, configurable models):
  reasoning   -> openai/gpt-5            (a reasoning model: needs a LARGE max_tokens,
                                          small budgets return empty content)
  translation -> anthropic/claude-sonnet-5
  reviewer    -> translator model by default

Each agent supports a LIVE backend (real call) and an OFFLINE backend (canned responses,
for deterministic loop/control-flow tests without spending API calls).
"""
from __future__ import annotations
import os
from dataclasses import dataclass
from typing import Callable, List, Optional, Union

from .models import AcceptedKnowledge, AttemptKind
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
    # Optional distinguishes missing provider accounting from a genuine zero.
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    reasoning_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    cost_credits: Optional[float] = None


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
    if reasoning_effort is not None:
        # OpenRouter's unified reasoning parameter is not part of the OpenAI SDK's
        # typed chat-completions surface, so forward it in the request body.
        kw["extra_body"] = {"reasoning": {"effort": reasoning_effort}}
    if timeout is not None:                    # per-call bound (loop caps by remaining runtime budget)
        kw["timeout"] = timeout
    r = _client().chat.completions.create(**kw)
    u = r.usage
    completion_details = getattr(u, "completion_tokens_details", None)
    prompt_details = getattr(u, "prompt_tokens_details", None)
    choices = getattr(r, "choices", None)
    if not choices:
        raise RuntimeError(
            "provider returned no completion choices; check available credits "
            "and provider status"
        )
    message = getattr(choices[0], "message", None)
    if message is None:
        raise RuntimeError("provider returned a completion without a message")
    return LLMResult(
        prompt=prompt,
        text=(getattr(message, "content", None) or "").strip(),
        model=r.model,
        prompt_tokens=getattr(u, "prompt_tokens", None),
        completion_tokens=getattr(u, "completion_tokens", None),
        total_tokens=getattr(u, "total_tokens", None),
        reasoning_tokens=getattr(completion_details, "reasoning_tokens", None),
        cached_tokens=getattr(prompt_details, "cached_tokens", None),
        cost_credits=getattr(u, "cost", None),
    )


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
        informal_progress: List[AcceptedKnowledge],
        *,
        problem_mode: str,
        failure_feedback: str = "",
        failed_next_step: str = "",
        future_ideas: str = "",
    ) -> str:
        if problem_mode not in ("easy", "hard"):
            raise ValueError(f"unsupported problem mode: {problem_mode!r}")
        proved_steps = "\n\n".join(
            f"Step {index}:\n{item.statement}"
            for index, item in enumerate(informal_progress, start=1)
        ) or "(none yet)"
        parts = [
            (
                "You are solving a hard math problem.\n\n"
                "However, instead of generating the whole solution at once, your task is to "
                "assess the current progress and suggest the next step.\n"
            ),
            "\n## Problem statement:\n" + informal_problem,
            (
                "\n## Previously proved steps\n\n"
                + proved_steps
                + "\n\nEvery listed step has been formally verified. Reassess which "
                "steps are relevant as the proof direction changes; you do not need "
                "to use every proved fact."
            ),
            (
                "\n## Ideas for the future (planning context; not yet proved)\n"
                + (future_ideas or "(none yet)")
                + "\n\nTreat these only as a rolling roadmap. Reassess whether the "
                "direction is promising, carry forward useful unfinished ideas, and "
                "revise or discard stale ones. You do not need to use every proved fact."
            ),
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
                "<assess current progress and the general direction, whether that direction is promising, and how the proposed step advances it. Notice irrelevant or redundant prior steps.>\n\n"
                "NEXT STEP:\n"
                "<A statement of the next step, along with a small reason why it's true (summarized proof)>\n\n"
                "PROOF:\n"
                "<a non-empty English detailed proof of that step>\n\n"
                "IS_FINAL_STEP: True | False\n\n"
                "IDEAS FOR THE FUTURE:\n"
                "<a non-empty rolling roadmap of likely later moves, preferably with [High], [Medium], or [Low] confidence on each idea; use `None — the theorem is complete` for a final step>\n\n"
                "Use `True` exactly when this step completes the theorem; otherwise use "
                "`False`. IDEAS FOR THE FUTURE must be the final section."
            )
        else:
            parts.append(
                "\n## Required action format\n\n"
                "For a non-final action, return exactly:\n\n"
                "INTERMEDIATE REASONING:\n"
                "<assess current progress and test or assert the general direction, whether that direction is promising, and elaborate on the next steps that would contribute on the completion of this problem. Notice irrelevant or redundant prior steps.>\n\n"
                "NEXT STEP:\n"
                "<A statement of the next step, along with a small reason why it's true (summarized proof)>\n\n"
                "PROOF:\n"
                "<a non-empty English detailed proof of that step>\n\n"
                "IS_FINAL_STEP: False\n\n"
                "IDEAS FOR THE FUTURE:\n"
                "<a non-empty rolling roadmap of likely later moves, preferably with [High], [Medium], or [Low] confidence on each idea>\n\n"
                "For a final action, return exactly:\n\n"
                "INTERMEDIATE REASONING:\n"
                "<English reasoning.>\n\n"
                "NEXT STEP:\n"
                "<one non-empty English proof step>\n\n"
                "PROOF:\n"
                "<a non-empty English proof of that step>\n\n"
                "IS_FINAL_STEP: True\n\n"
                "ANSWER:\n"
                "<the concrete mathematical answer in English or mathematical notation>\n\n"
                "IDEAS FOR THE FUTURE:\n"
                "None — the theorem is complete\n\n"
                "ANSWER is required and non-empty exactly for a final action. It is "
                "forbidden for a non-final action. IDEAS FOR THE FUTURE must always "
                "be the final section."
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
                 max_tokens: int = 32000,
                 reasoning_effort: Optional[str] = None):
        self.model = model
        self.considerations = considerations
        self.offline = offline_responses
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort
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
                "  <Updated theorem proof; it may use the new lemmas and objects in NEW DECLARATIONS, and it may contain `sorry`>"
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
            "<intermediate reasoning - assess the current Lean 4 file and next English "
            "step and proof, and reason about how to translate to Lean 4.>\n\n"
            "PLAN:\n"
            "<state which lemmas or definitions you will propose, how you will prove "
                "them, and how you will change the theorem body>\n\n"
            "NEW DECLARATIONS:\n"
            "```lean4\n"
            "<New lemmas along with their proofs, any other declarations like object definitions, or an empty fence>\n"
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
                "- The INTERMEDIATE REASONING and PLAN sections are mandatory, non-empty, "
                "and validated before any translation decision is accepted. In the reasoning, "
                "identify the exact Lean proposition, inventory the usable hypotheses and "
                "definitions, consider the provided proof, which could be a very important "
                "guide for which path you will take for your Lean 4 proofs, "
                "consider a library-lemma route and/or an unfold/algebra fallback, "
                "then choose a concrete route.\n"
                "- In exploration, the one inherited final theorem `sorry` may remain, "
                "but never use `sorry` or `admit` inside a local `have`, nested proof, or any new "
                "fact. Do not introduce additional proof holes.\n"
                "- Reject only when the English step is mathematically incorrect, lacks "
                "an assumption, or conflicts with the formal context. Difficulty is not a "
                "valid rejection reason: if an exact library lemma is unknown or the proof "
                "needs tedious vector/algebraic work, build the needed helper definitions "
                "and lemmas and submit a concrete Lean attempt for compiler feedback.\n\n"
                "- An exploration transaction must make formal proof progress. Definitions "
                "alone do not establish the English step: if the theorem body is unchanged, "
                "NEW DECLARATIONS must contain a proved theorem or lemma formalizing the step. "
                "An unused definition plus the inherited final `sorry` will be rejected.\n\n"
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
            (
                "\n## Graceful rejection alternative\n\n"
                "Instead of the Lean transaction, you may return exactly the following "
                "when the candidate is mathematically or contextually invalid:\n\n"
                "INTERMEDIATE REASONING:\n"
                "<check the exact claim against the hypotheses and formal context before deciding; non-empty>\n\n"
                "TRANSLATION REJECTED:\n"
                "KIND: <MATHEMATICALLY_INCORRECT | MISSING_ASSUMPTION | "
                "INCOMPATIBLE_WITH_CONTEXT>\n"
                "REASON:\n"
                "<a non-empty English explanation identifying the precise defect>\n\n"
                "A rejection must contain no Lean transaction sections. "
                "HARD_TO_FORMALIZE is not a valid rejection: formalization difficulty "
                "must produce a Lean transaction so the compiler can guide retries."
            ),
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
            feedback_heading = (
                "### Semantic reviewer feedback"
                if repair_context.failed_check == "semantic_alignment"
                else "### All compiler errors and their source locations"
            )
            repair_instruction = (
                "Repair every semantic gap identified by the reviewer. The replacement must "
                "still compile, but compilation alone is insufficient: prove the reasoner's "
                "entire stated step without weakening it or assuming it through a proxy."
                if repair_context.failed_check == "semantic_alignment"
                else "Fix every compiler error listed above. Use the reported source region and "
                     "numbered excerpt to repair the exact failing expression."
            )
            parts.append(
                "\n## Previous rejected translation — repair this exact output\n\n"
                f"The `{repair_context.failed_check}` check failed. The complete previous "
                "translator response is reproduced below.\n\n"
                "### Complete previous model output\n\n"
                "<previous_model_output>\n" + repair_context.raw_output.rstrip() + "\n</previous_model_output>\n\n"
                + feedback_heading + "\n\n"
                + repair_context.diagnostics.rstrip() + "\n\n"
                "### Required repair behavior\n\n"
                + repair_instruction + " Preserve unrelated "
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
        return _call(self.model, prompt, self.max_tokens,
                     reasoning_effort=self.reasoning_effort, timeout=timeout)


# ---------------- SemanticReviewerAgent ----------------
class SemanticReviewerAgent:
    """Fail-closed LLM gate between Lean checkpoint success and acceptance."""

    def __init__(self, model: str, considerations: str,
                 offline_responses: Optional[List[OfflineResponse]] = None,
                 max_tokens: int = 16000,
                 reasoning_effort: Optional[str] = None):
        self.model = model
        self.considerations = considerations
        self.offline = offline_responses
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort

    def build_prompt(
        self,
        informal_problem: str,
        informal_candidate: str,
        current_file: str,
        translator_output: str,
        candidate_file: str,
        *,
        attempt_kind: AttemptKind,
        answer: Optional[str] = None,
    ) -> str:
        answer_section = (
            "\n## Proposed concrete answer\n\n" + answer.strip()
            if answer else ""
        )
        return (
            "You are the semantic alignment gate in a Lean 4 proof-search system. "
            "The proposed transaction has already passed its Lean declaration and theorem-body "
            "checkpoint checks. Decide whether it fully formalizes the reasoner's exact English "
            "step, not merely whether it compiles or makes related progress.\n\n"
            "Return MISALIGNED when the transaction proves only a useful sublemma, weakens "
            "quantifiers/hypotheses/domain, assumes or repackages the desired result in a new "
            "definition or premise, omits a base case or induction/semantic bridge, or leaves the "
            "requested claim unconnected to the repository's actual formal definitions. A partial "
            "formalization is a rejection even when it may be useful later. For finalization, also "
            "check that the filled answer and final theorem express the proposed English answer.\n\n"
            "Return exactly:\n\n"
            "INTERMEDIATE REASONING:\n"
            "<compare the requested claims and proof obligations against the compiled Lean propositions>\n\n"
            "VERDICT: <ALIGNED | MISALIGNED>\n\n"
            "FEEDBACK:\n"
            "<None if aligned; otherwise list the precise missing or weakened obligations and how the translator must repair them>\n\n"
            "## Problem statement\n\n" + informal_problem +
            "\n\n## Reasoner's exact step and proof\n\n" + informal_candidate +
            "\n\n## Attempt kind\n\n" + attempt_kind + answer_section +
            "\n\n## Formal file before this transaction\n\n```lean4\n" + current_file.rstrip() +
            "\n```\n\n## Translator's complete response\n\n<translator_output>\n" +
            translator_output.rstrip() +
            "\n</translator_output>\n\n## Compiling candidate file\n\n```lean4\n" +
            candidate_file.rstrip() + "\n```\n\n## Review considerations\n\n" +
            self.considerations
        )

    def review(self, *args, timeout: float = None,
               prepared_prompt: Optional[str] = None, **kw) -> LLMResult:
        prompt = prepared_prompt if prepared_prompt is not None else self.build_prompt(*args, **kw)
        if self.offline is not None:
            text = self.offline.pop(0) if self.offline else ""
            if callable(text):
                text = text(prompt)
            if isinstance(text, Exception):
                raise text
            return LLMResult(prompt, text, self.model + "[offline]")
        return _call(self.model, prompt, self.max_tokens,
                     reasoning_effort=self.reasoning_effort, timeout=timeout)
