#!/usr/bin/env python3
"""Deterministic semantic-review gate, retry, feedback, and resume coverage."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from midas.loop import resume_problem, run_problem
from midas.parser import parse_semantic_review


def reason(step: str, proof: str, *, final=False) -> str:
    return (
        f"NEXT STEP:\n{step}\n\nPROOF:\n{proof}\n\n"
        "STEP USEFULNESS:\nHigh\n\n"
        f"IS_FINAL_STEP: {'True' if final else 'False'}\n\n"
        "IDEAS FOR THE FUTURE:\n" +
        ("None — the theorem is complete" if final else "[High] Finish next.")
    )


def transaction(declarations: str, body: str, *, final=False) -> str:
    heading = "FINAL THEOREM BODY" if final else "UPDATED THEOREM BODY"
    return (
        "INTERMEDIATE REASONING:\nTranslate the requested statement.\n\n"
        "PLAN:\nProve the needed declaration and update the body.\n\n"
        f"NEW DECLARATIONS:\n```lean4\n{declarations}\n```\n\n"
        f"{heading}:\n```lean4\n{body}\n```"
    )


ALIGNED = (
    "INTERMEDIATE REASONING:\nEvery material English claim is proved by the transaction.\n\n"
    "VERDICT: ALIGNED\n\nFEEDBACK:\nNone"
)
MISALIGNED = (
    "INTERMEDIATE REASONING:\nThe declaration proves only True, not A = 5.\n\n"
    "VERDICT: MISALIGNED\n\nFEEDBACK:\nProve the requested equality A = 5; "
    "a theorem of type True is only unrelated formal progress."
)


def make_problem(tmp: Path, name: str, *, translations=2, candidates=1) -> Path:
    problem = tmp / name
    (problem / "input").mkdir(parents=True)
    (problem / "config.json").write_text(json.dumps({
        "max_proof_steps": 3,
        "max_informal_candidates_per_proof_step": candidates,
        "max_lean_translation_attempts_per_candidate": translations,
        "max_total_lean_attempts": 10,
        "max_runtime_seconds": 120,
        "lean_prelude": [],
        "reasoning_model": "offline-reasoner",
        "translation_model": "offline-translator",
        "reviewer_model": "offline-reviewer",
        "max_reviewer_call_attempts": 2,
    }))
    (problem / "input" / "informal_problem.md").write_text("Prove A equals five.")
    (problem / "input" / "context.lean").write_text("def A : Nat := 2 + 3\n")
    (problem / "input" / "body_initial.lean").write_text(
        "theorem main : A = 5 := by\n  sorry\n"
    )
    return problem


rows = []


def check(label, condition):
    rows.append((label, bool(condition)))


# Unit protocol checks.
check("aligned review parses", parse_semantic_review(ALIGNED).verdict == "ALIGNED")
check("misaligned review parses", parse_semantic_review(MISALIGNED).verdict == "MISALIGNED")
check("malformed review is rejected", not parse_semantic_review("VERDICT: ALIGNED").ok)

tmp = Path(tempfile.mkdtemp(prefix="midas-semantic-review-"))
try:
    runs = tmp / "runs"
    problem = make_problem(tmp, "alignment")
    seen_repair = {}

    def repaired_translation(prompt):
        seen_repair["prompt"] = prompt
        return transaction(
            "theorem A_eq_five : A = 5 := by decide",
            "theorem main : A = 5 := by\n  exact A_eq_five",
        )

    state = run_problem(
        str(problem), runs_root=str(runs),
        reasoning_offline=[
            reason("Prove A = 5.", "A unfolds to 2 + 3."),
            reason("Finish using A = 5.", "Use the established equality.", final=True),
        ],
        translation_offline=[
            transaction(
                "theorem irrelevant_progress : True := by trivial",
                "theorem main : A = 5 := by\n  sorry",
            ),
            repaired_translation,
            transaction("", "theorem main : A = 5 := by\n  decide", final=True),
        ],
        reviewer_offline=[MISALIGNED, ALIGNED, ALIGNED],
    )
    run = runs / "alignment"
    rejected_dir = run / "artifacts/proof_steps/proof_step_001/informal_candidate_001/lean4_attempt_001"
    rejected = json.loads((rejected_dir / "compile.json").read_text())
    check("run succeeds only after aligned retry", state.status == "final_success")
    check("compiling partial transaction is semantic_misalignment",
          rejected["attempt_status"] == "semantic_misalignment")
    check("Lean passed before semantic rejection",
          rejected["declaration_check"]["status"] == "passed" and
          rejected["body_check"]["status"] == "passed")
    check("semantic report persists feedback",
          rejected["semantic_review"]["verdict"] == "MISALIGNED" and
          "A = 5" in rejected["semantic_review"]["feedback"])
    check("review feedback reaches translator repair prompt",
          "semantic_alignment" in seen_repair.get("prompt", "") and
          "theorem of type True" in seen_repair.get("prompt", ""))
    check("rejected declaration never becomes authoritative",
          "irrelevant_progress" not in
          (run / "accepted/proof_step_001/declarations.lean").read_text())

    # Invalid reviewer output is retried with the identical request.
    retry_problem = make_problem(tmp, "review_retry", translations=1)
    retry_state = run_problem(
        str(retry_problem), runs_root=str(runs),
        reasoning_offline=[reason("Finish A = 5.", "Compute it.", final=True)],
        translation_offline=[transaction("", "theorem main : A = 5 := by\n  decide", final=True)],
        reviewer_offline=["invalid", ALIGNED],
    )
    retry_root = runs / "review_retry/artifacts/proof_steps/proof_step_001/informal_candidate_001/lean4_attempt_001/semantic_reviews"
    prompt1 = (retry_root / "reviewer_attempt_001/reviewer_prompt.md").read_text()
    prompt2 = (retry_root / "reviewer_attempt_002/reviewer_prompt.md").read_text()
    check("invalid reviewer response retries identical prompt",
          retry_state.status == "final_success" and prompt1 == prompt2)

    # Two infrastructure failures fail closed and never publish a final file.
    closed_problem = make_problem(tmp, "fail_closed", translations=1)
    closed_state = run_problem(
        str(closed_problem), runs_root=str(runs),
        reasoning_offline=[reason("Finish A = 5.", "Compute it.", final=True)],
        translation_offline=[transaction("", "theorem main : A = 5 := by\n  decide", final=True)],
        reviewer_offline=[RuntimeError("review outage"), RuntimeError("review outage")],
    )
    closed_attempt = closed_state.proof_steps[0].informal_candidates[0].lean_translation_attempts[0]
    check("review outage fails closed after two calls",
          closed_state.status == "failed" and
          closed_attempt.status == "reviewer_call_failed" and
          len(closed_attempt.semantic_review_attempts) == 2)
    check("unreviewed final is not published",
          not (runs / "fail_closed/final/solution.lean").exists())

    # Interrupting a reviewer resumes the saved translation without another translator call.
    resume_problem_dir = make_problem(tmp, "review_resume", translations=1)

    def interrupt_review(_prompt):
        raise KeyboardInterrupt("simulated ctrl-c")

    try:
        run_problem(
            str(resume_problem_dir), runs_root=str(runs),
            reasoning_offline=[reason("Finish A = 5.", "Compute it.", final=True)],
            translation_offline=[transaction("", "theorem main : A = 5 := by\n  decide", final=True)],
            reviewer_offline=[interrupt_review],
        )
    except KeyboardInterrupt:
        pass
    interrupted_state = json.loads((runs / "review_resume/state.json").read_text())
    calls_before = interrupted_state["stats"]["total_llm_calls"]
    lean_attempts_before = interrupted_state["stats"]["total_lean_attempts"]
    resumed = resume_problem(
        str(runs / "review_resume"),
        reasoning_offline=[], translation_offline=[], reviewer_offline=[ALIGNED],
    )
    check("review resume succeeds without another translator attempt",
          resumed.status == "final_success" and
          resumed.stats.total_lean_attempts == lean_attempts_before and
          resumed.stats.total_llm_calls == calls_before + 1)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

for label, ok in rows:
    print(f"{label:<68}{'PASS' if ok else 'FAIL'}")
all_ok = all(ok for _, ok in rows)
print(f"\nSEMANTIC REVIEWER: {'PASS' if all_ok else 'FAIL'}")
sys.exit(0 if all_ok else 1)
