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
from typing import List, Optional

from .reconstructor import reconstruct

OPENROUTER_BASE = "https://openrouter.ai/api/v1"


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
                 offline_responses: Optional[List[str]] = None, max_tokens: int = 16000,
                 reasoning_effort: str = "low"):
        self.model = model
        self.considerations = considerations
        self.offline = offline_responses
        self.max_tokens = max_tokens
        self.reasoning_effort = reasoning_effort

    def build_prompt(self, informal_problem: str, informal_progress: str, knowledge: List[str],
                     context_summary: str, accepted_decls_summary: str, current_body: str,
                     failure_feedback: str = "", failed_next_step: str = "") -> str:
        parts = [
            (
                "You are solving a hard math problem.\n\n"
                "However, instead of generating the whole solution at once, your task is to "
                "assess the current progress and suggest the next step.\n"
            ),
            "\n## Problem statement:\n" + informal_problem,
            "\n## Current progress (assume everything here has been already proved)\n" + (informal_progress or "(none yet)"),
        #   "\n## Current knowledge: \n" + ("\n".join(f"- {k}" for k in knowledge) or "(none)"),
        ]
        if failed_next_step:
            parts.append("\n## Previous step that could not be translated to Lean\n\n" +
                         failed_next_step)
        if failure_feedback:
            parts.append("\n## Feedback\n" + failure_feedback)
        parts.append("\n## Considerations\n" + self.considerations)
        return "\n".join(parts)

    def propose(self, *args, attempt_index: int = 0, timeout: float = None, **kw) -> LLMResult:
        prompt = self.build_prompt(*args, **kw)
        if self.offline is not None:                       # sequential canned queue
            text = self.offline.pop(0) if self.offline else ""
            return LLMResult(prompt, text, self.model + "[offline]")
        return _call(self.model, prompt, self.max_tokens,
                     reasoning_effort=self.reasoning_effort, timeout=timeout)


# ---------------- TranslationAgent (§10) ----------------
class TranslationAgent:
    def __init__(self, model: str, considerations: str,
                 offline_responses: Optional[List[str]] = None, max_tokens: int = 8000):
        self.model = model
        self.considerations = considerations
        self.offline = offline_responses
        self.max_tokens = max_tokens

    def build_prompt(self, header: str, informal_problem: str, prelude: List[str], context: str,
                     accepted_decls: List[str], current_body: str,
                     informal_candidate: str, compiler_feedback: str = "") -> str:
        current_file = reconstruct(prelude, context, accepted_decls,
                                   "" + (current_body or "").strip())
        parts = [
            (
                "You are part of a system that converts an English mathematical proof into a Lean 4 proof.\n\n"
                "The English proof is given one step at a time. You will be given the next step, and your task is to update the "
                "current Lean 4 proof state so that it reflects the step.\n\n"
                "You may do this by:\n"
                "1. adding new Lean lemmas or definitions, and/or\n"
                "2. replacing the current theorem body with an updated theorem body.\n\n"
                "Your task is to do the following:\n"
                "- Reason about the current Lean 4 file below, along with the next English "
                "step, and what steps can be done to translate the next English step to "
                "Lean 4, while following translation structure of adding new lemmas and "
                "updating the theorem body.\n"
                "- Output your new lemmas in a section called NEW DECLARATIONS. These "
                "lemmas will be added to the Lean 4 file and can depend on previous lemmas.\n"
                "- Output the updated theorem body in a section called UPDATED THEOREM "
                "BODY. Keep the statement exactly the same - only the proof can be changed.\n\n"
                "Follow this structure for the output:\n\n"
                "INTERMEDIATE REASONING:\n"
                "<intermediate reasoning - assess the current Lean 4 file and next English "
                "step, and reason about how to translate to Lean 4>\n\n"
                "NEW DECLARATIONS:\n"
                "<New lemmas along with their proofs>\n\n"
                "UPDATED THEOREM BODY:\n"
                "```lean4\n"
                f"{header}\n"
                "  <Updated theorem proof, it may use the new lemmas and objects in NEW DECLARATIONS, and can use 'sorry' statements>\n"
                "```"
            ),
            "\n## Problem statement in English:\n" + informal_problem,
            "\n\n## Current Lean 4 file\n```lean4\n" + current_file + "```",
            (
                "\nThe last theorem is the current theorem body. "
                "Your new lemmas and/or definitions in NEW DECLARATIONS will be added " 
                "before that theorem body. Do not repeat imports, "
                "context definitions, or already accepted declarations in NEW DECLARATIONS."
            ),
            "\n## Informal step to translate (with its proof)\n" + informal_candidate,
        ]
        if compiler_feedback:
            parts.append("\n## Compiler feedback from the previous attempt (fix this)\n" + compiler_feedback)

        parts.append("\n## Considerations\n" + self.considerations)
        return "\n".join(parts)

    def translate(self, *args, attempt_index: int = 0, timeout: float = None, **kw) -> LLMResult:
        prompt = self.build_prompt(*args, **kw)
        if self.offline is not None:                       # sequential canned queue
            text = self.offline.pop(0) if self.offline else ""
            return LLMResult(prompt, text, self.model + "[offline]")
        return _call(self.model, prompt, self.max_tokens, timeout=timeout)
