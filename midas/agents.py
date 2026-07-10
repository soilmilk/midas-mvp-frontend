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
                     failure_feedback: str = "") -> str:
        parts = [
            (
                "You are solving a hard math problem.\n\n"
                "However, instead of generating the whole solution at once, your task is to"
                "assess the current progress and suggest the next step\n\n"
            ),
            "\n## Informal problem\n" + informal_problem,
            "\n## Current informal progress\n" + (informal_progress or "(none yet)"),
            "\n## Current knowledge: \n" + ("\n".join(f"- {k}" for k in knowledge) or "(none)"),
        ]
        if failure_feedback:
            parts.append("\n## Feedback\n" + failure_feedback)
        parts.append(
            "\nSuggest ONE proof step that should be translatable to Lean 4, along with its proof.\n"
            "Avoid large jumps. Do not propose a complete proof unless the theorem is clearly almost "
            "finished. Output EXACTLY this shape:\n\n"
            "[intermediate reasoning]\n\nNEXT STEP:\n<one small step>\n\nPROOF:\n<informal proof of that step>")
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

    def build_prompt(self, header: str, context: str, accepted_decls: str, current_body: str,
                     informal_candidate: str, compiler_feedback: str = "") -> str:
        parts = [
            (
                "You are part of a system that converts an English mathematical proof into a Lean 4 proof.\n\n"
                "The English proof is given one step at a time. You will be given the next step, and your task is to update the "
                "current Lean 4 proof state so that it reflects the step.\n\n"
                "You may do this by:\n"
                "1. adding new Lean lemmas or definitions, and/or\n"
                "2. replacing the current theorem body with an updated theorem body.\n\n"
            ),
            "\n## Original formal theorem header (copy EXACTLY, byte-for-byte)\n```\n" + header + "\n```",
            "\n## Fixed input/context.lean (already available, do not restate/import)\n```lean4\n" + context + "\n```",
            "\n## Current accepted Lean declarations (available; do not repeat)\n" + (accepted_decls or "(none)"),
            "\n## Current theorem body\n```lean4\n" + current_body + "\n```",
            "\n## Informal step to translate (with its proof)\n" + informal_candidate,
        ]
        if compiler_feedback:
            parts.append("\n## Compiler feedback from the previous attempt (fix this)\n" + compiler_feedback)
        parts.append(
            "\nOutput intermediate reasoning if you like, then EXACTLY these two required sections:\n\n"
            "NEW DECLARATIONS:\n```lean4\n-- descriptive comment\n<new lemmas/defs, or leave empty>\n```\n\n"
            "UPDATED THEOREM BODY:\n\n```\n<full theorem declaration, header copied exactly, only the "
            "proof after ':= by' changed>\n```\n\n"
            "Rules: NEW DECLARATIONS is a delta (may be empty), no sorry, no imports, don't repeat "
            "accepted declarations. UPDATED THEOREM BODY is the complete theorem, header byte-exact, "
            "may contain sorry unless this is the final step.")
        parts.append("\n## Considerations\n" + self.considerations)
        return "\n".join(parts)

    def translate(self, *args, attempt_index: int = 0, timeout: float = None, **kw) -> LLMResult:
        prompt = self.build_prompt(*args, **kw)
        if self.offline is not None:                       # sequential canned queue
            text = self.offline.pop(0) if self.offline else ""
            return LLMResult(prompt, text, self.model + "[offline]")
        return _call(self.model, prompt, self.max_tokens, timeout=timeout)
