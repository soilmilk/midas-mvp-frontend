"""
Pydantic v2 models matching SPEC.md §2 (config), §5 (state hierarchy), §17 (statuses),
and §19 (compile.json). Python 3.9-safe (Optional, from __future__ annotations).
"""
from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, model_validator


AttemptKind = Literal[
    "exploration",
    "easy_finalization",
    "hard_finalization",
]

ReasoningEffort = Literal[
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
]


def attempt_kind_for(problem_mode: str, is_final_step: bool) -> AttemptKind:
    """Derive the translator protocol solely from mode and the reasoner signal."""
    if problem_mode not in ("easy", "hard"):
        raise ValueError(f"unsupported problem mode: {problem_mode!r}")
    if not is_final_step:
        return "exploration"
    return "hard_finalization" if problem_mode == "hard" else "easy_finalization"


# ---------------- config (§2) ----------------
class Config(BaseModel):
    problem_mode: Literal["easy", "hard"] = "easy"
    max_proof_steps: int = 40
    max_informal_candidates_per_proof_step: int = 3
    max_lean_translation_attempts_per_candidate: int = 3
    max_total_lean_attempts: int = 300
    max_runtime_seconds: int = 3600
    lean_prelude: List[str] = Field(default_factory=list)   # full Lean lines, not module names
    # not in the spec's example but referenced by §9/§10 — OpenRouter model slugs, overridable
    reasoning_model: str = "openai/gpt-5"
    translation_model: str = "anthropic/claude-sonnet-5"
    # gpt-5 is a reasoning model; latency = reasoning tokens. minimal(~1.5s) < low(~6s) < medium/high.
    reasoning_effort: ReasoningEffort = "low"
    # None leaves the translator model/provider default in effect. Set this explicitly
    # for thinking models so hidden reasoning cannot unexpectedly consume the response budget.
    translator_reasoning_effort: Optional[ReasoningEffort] = None
    # "fresh" = fresh `lean` per checkpoint (default). "warm" = in-repo warm server
    # (Mathlib resident, paid once) — for a Mathlib prelude. See INTEGRATION.md.
    verifier_backend: str = "fresh"
    warm_binary: str = ""        # optional path to `warm` exe; defaults to warm-server/.lake/build/bin/warm
    warm_lean_path: str = ""     # LEAN_PATH to the Mathlib oleans (warm backend only)


# ---------------- compile.json (§19) ----------------
class Diagnostic(BaseModel):
    file: str = ""
    line: int = 0
    col: int = 0
    severity: str = "error"          # error | warning
    code: str = ""                   # Lean 4.31 diagnostic code, e.g. lean.unknownIdentifier
    message: str = ""
    source_region: str = ""
    nearby_code: str = ""


class CheckReport(BaseModel):
    status: str = "not_run"          # passed | failed | not_run
    errors: List[Diagnostic] = Field(default_factory=list)


class CompileJson(BaseModel):
    attempt_status: str              # §17 lean_translation_attempt.status
    attempt_kind: AttemptKind = "exploration"
    translator_output_empty: bool = False
    translation_call_error: str = ""
    translator_rejection_kind: str = ""
    translator_rejection_reason: str = ""
    structure_check: CheckReport = Field(default_factory=CheckReport)
    declaration_check: CheckReport = Field(default_factory=CheckReport)
    body_check: CheckReport = Field(default_factory=CheckReport)
    final_check: CheckReport = Field(default_factory=CheckReport)
    raw_verifier_output: str = ""


# ---------------- state hierarchy (§5, §17) ----------------
class LeanTranslationAttempt(BaseModel):
    lean_translation_attempt_index: int
    # pending | translation_call_failed | translator_rejected_step
    #        | parse_error | format_failed | unproved_body_fact | no_formal_progress
    #        | lemma_failed | body_failed | accepted
    #        | placeholder_format_failed | placeholder_fill_failed
    #        | final_success | final_reconstruction_failed
    #        | hard_full_reconstruction_failed
    # parse_error  = raw output could not be parsed into the required sections (§11)
    # format_failed = parsed OK but broke a structure rule (§8/§12)
    status: str = "pending"
    translator_prompt_path: Optional[str] = None
    raw_translator_output_path: Optional[str] = None
    declarations_path: Optional[str] = None
    placeholder_path: Optional[str] = None
    body_path: Optional[str] = None
    compile_path: Optional[str] = None
    declaration_check_input_path: Optional[str] = None
    body_check_input_path: Optional[str] = None
    final_check_input_path: Optional[str] = None
    attempt_kind: AttemptKind = "exploration"
    proposed_final_answer: Optional[str] = None
    translator_rejection_kind: str = ""
    translator_rejection_reason: str = ""


class InformalCandidate(BaseModel):
    informal_candidate_index: int
    status: str = "pending"          # pending | accepted | abandoned
    reasoning_prompt_path: Optional[str] = None
    informal_step_path: Optional[str] = None
    reasoning_call_error_path: Optional[str] = None
    step_usefulness: Optional[Literal["High", "Medium", "Low"]] = None
    future_ideas: str = ""
    lean_translation_attempts: List[LeanTranslationAttempt] = Field(default_factory=list)


class AcceptedKnowledge(BaseModel):
    """One Lean-verified English step and its relevance estimate when proposed."""
    statement: str
    step_usefulness: Optional[Literal["High", "Medium", "Low"]] = None


class ProofStep(BaseModel):
    proof_step_index: int
    status: str = "pending"          # pending | accepted | failed | final_success
    informal_candidates: List[InformalCandidate] = Field(default_factory=list)


class LLMUsageStats(BaseModel):
    """Provider-reported accounting; missing usage is never estimated."""
    calls: int = 0
    calls_with_token_usage: int = 0
    calls_with_cost: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cost_credits: float = 0.0


class LLMUsageBreakdown(BaseModel):
    total: LLMUsageStats = Field(default_factory=LLMUsageStats)
    reasoner: LLMUsageStats = Field(default_factory=LLMUsageStats)
    translator: LLMUsageStats = Field(default_factory=LLMUsageStats)


class RunStats(BaseModel):
    accepted_proof_steps: int = 0
    total_lean_attempts: int = 0
    started_at: str = ""
    runtime_seconds: float = 0.0
    total_llm_calls: int = 0
    llm_usage: LLMUsageBreakdown = Field(default_factory=LLMUsageBreakdown)
    total_lean_compiles: int = 0

    @model_validator(mode="before")
    @classmethod
    def backfill_legacy_llm_calls(cls, data):
        """Old states know attempted calls even though they have no usage totals."""
        if isinstance(data, dict) and "llm_usage" not in data:
            calls = int(data.get("total_llm_calls") or 0)
            if calls:
                data = dict(data)
                data["llm_usage"] = {"total": {"calls": calls}}
        return data


class ProofRunState(BaseModel):
    problem_id: str
    informal_problem_path: str
    context_path: str
    initial_body_path: str
    problem_mode: str = "easy"
    placeholder_path: Optional[str] = None
    placeholder_initial_source: Optional[str] = None
    placeholder_header: Optional[str] = None
    placeholder_name: Optional[str] = None
    placeholder_status: Optional[str] = None
    placeholder_final_source: Optional[str] = None
    formal_theorem_header: str = ""
    current_knowledge: List[AcceptedKnowledge] = Field(default_factory=list)
    future_ideas: str = ""
    status: str = "running"          # running | final_success | failed
    failure_reason: Optional[str] = None
    stats: RunStats = Field(default_factory=RunStats)
    proof_steps: List[ProofStep] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_knowledge(cls, data):
        """Keep old state.json files with string-only knowledge readable."""
        if isinstance(data, dict) and isinstance(data.get("current_knowledge"), list):
            data = dict(data)
            data["current_knowledge"] = [
                {"statement": item, "step_usefulness": None}
                if isinstance(item, str) else item
                for item in data["current_knowledge"]
            ]
        return data


ACCEPTED_ATTEMPT_STATUSES = {"accepted", "final_success"}
ACCEPTED_STEP_STATUSES = {"accepted", "final_success"}
