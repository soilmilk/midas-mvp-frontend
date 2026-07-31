"""
Pydantic v2 models matching SPEC.md §2 (config), §5 (state hierarchy), §17 (statuses),
and §19 (compile.json). Python 3.9-safe (Optional, from __future__ annotations).
"""
from __future__ import annotations
from typing import List, Literal, Optional
from pydantic import BaseModel, Field


AttemptKind = Literal[
    "exploration",
    "easy_finalization",
    "hard_finalization",
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
    reasoning_effort: str = "low"
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
    structure_check: CheckReport = Field(default_factory=CheckReport)
    declaration_check: CheckReport = Field(default_factory=CheckReport)
    body_check: CheckReport = Field(default_factory=CheckReport)
    final_check: CheckReport = Field(default_factory=CheckReport)
    raw_verifier_output: str = ""


# ---------------- state hierarchy (§5, §17) ----------------
class LeanTranslationAttempt(BaseModel):
    lean_translation_attempt_index: int
    # pending | translation_call_failed | parse_error | format_failed
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


class InformalCandidate(BaseModel):
    informal_candidate_index: int
    status: str = "pending"          # pending | accepted | abandoned
    reasoning_prompt_path: Optional[str] = None
    informal_step_path: Optional[str] = None
    reasoning_call_error_path: Optional[str] = None
    lean_translation_attempts: List[LeanTranslationAttempt] = Field(default_factory=list)


class ProofStep(BaseModel):
    proof_step_index: int
    status: str = "pending"          # pending | accepted | failed | final_success
    informal_candidates: List[InformalCandidate] = Field(default_factory=list)


class RunStats(BaseModel):
    accepted_proof_steps: int = 0
    total_lean_attempts: int = 0
    started_at: str = ""
    runtime_seconds: float = 0.0
    total_llm_calls: int = 0
    total_lean_compiles: int = 0


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
    current_knowledge: List[str] = Field(default_factory=list)
    status: str = "running"          # running | final_success | failed
    failure_reason: Optional[str] = None
    stats: RunStats = Field(default_factory=RunStats)
    proof_steps: List[ProofStep] = Field(default_factory=list)


ACCEPTED_ATTEMPT_STATUSES = {"accepted", "final_success"}
ACCEPTED_STEP_STATUSES = {"accepted", "final_success"}
