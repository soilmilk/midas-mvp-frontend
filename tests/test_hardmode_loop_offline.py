#!/usr/bin/env python3
"""Phase 3 Hard Mode routing, retry, rollback, and atomic-success gate."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from midas.artifacts import StateManager
from midas.loop import run_problem


CONTEXT = "def IsCorrectAnswer (n : Nat) : Prop := n = 5\n"
PLACEHOLDER_INITIAL = "def answer : Nat := sorry\n"
BODY_INITIAL = "theorem main : IsCorrectAnswer answer := sorry\n"
PLACEHOLDER_FINAL = "def answer : Nat := by\n  exact 5"
BODY_FINAL = "theorem main : IsCorrectAnswer answer := by\n  exact final_helper"
ALIGNED = (
    "INTERMEDIATE REASONING:\nThe compiled transaction proves the requested step.\n\n"
    "VERDICT: ALIGNED\n\nFEEDBACK:\nNone"
)


def action(next_step: str, proof: str, final=False, answer=None) -> str:
    raw = (
        "INTERMEDIATE REASONING:\nChoose the next useful fact.\n\n"
        f"NEXT STEP:\n{next_step}\n\n"
        f"PROOF:\n{proof}\n\n"
        f"IS_FINAL_STEP: {'True' if final else 'False'}"
    )
    if answer is not None:
        raw += f"\n\nANSWER:\n{answer}"
    raw += "\n\nIDEAS FOR THE FUTURE:\n" + (
        "None — the theorem is complete" if final else "[High] Continue from this fact."
    )
    return raw


def transaction(declarations: str, body: str, placeholder=None) -> str:
    sections = [
        f"NEW DECLARATIONS:\n```lean4\n{declarations}\n```",
    ]
    if placeholder is not None:
        sections.append(
            f"FILLED PLACEHOLDER:\n```lean4\n{placeholder}\n```"
        )
        body_heading = "FINAL THEOREM BODY"
    else:
        body_heading = "UPDATED THEOREM BODY"
    sections.append(f"{body_heading}:\n```lean4\n{body}\n```")
    return (
        "INTERMEDIATE REASONING:\nTranslate the action.\n\n"
        "PLAN:\nReturn the complete transaction.\n\n"
        + "\n\n".join(sections)
    )


def make_problem(root: Path, pid: str, *, candidates=2, translations=2) -> Path:
    problem = root / pid
    inp = problem / "input"
    inp.mkdir(parents=True)
    config = {
        "problem_mode": "hard",
        "max_proof_steps": 4,
        "max_informal_candidates_per_proof_step": candidates,
        "max_lean_translation_attempts_per_candidate": translations,
        "max_total_lean_attempts": 30,
        "max_runtime_seconds": 120,
        "lean_prelude": [],
        "reasoning_model": "offline",
        "translation_model": "offline",
    }
    (problem / "config.json").write_text(json.dumps(config))
    (inp / "informal_problem.md").write_text(
        "Find the natural number that equals five and prove it is correct."
    )
    (inp / "context.lean").write_text(CONTEXT)
    (inp / "placeholder.lean").write_text(PLACEHOLDER_INITIAL)
    (inp / "body_initial.lean").write_text(BODY_INITIAL)
    return problem


rows = []


def check(label, condition, detail=""):
    rows.append((label, bool(condition), detail))


tmp = Path(tempfile.mkdtemp(prefix="midas-hard-loop-"))
try:
    problem = make_problem(tmp, "hard_flow")
    runs = tmp / "runs"

    failed_final_step = "Commit the candidate answer now."
    failed_final_proof = "FAILED_FINAL_PROOF_SENTINEL"
    failed_final_answer = "FAILED_FINAL_ANSWER_SENTINEL"
    reasoning = [
        action(
            "Show that five satisfies the correctness predicate.",
            "The predicate unfolds to five equals five.",
        ),
        action(
            failed_final_step,
            failed_final_proof,
            final=True,
            answer=failed_final_answer,
        ),
        action(
            "Record the elementary equality five equals five.",
            "This follows by reflexivity.",
        ),
        action(
            "Use the established facts to finish with answer five.",
            "Fill the answer with five and apply the correctness lemma.",
            final=True,
            answer="5",
        ),
    ]

    open_body = BODY_INITIAL.strip()
    translations = [
        transaction(
            "theorem five_is_correct : IsCorrectAnswer 5 := by\n  rfl",
            open_body,
        ),
        transaction(
            "theorem premature_decl : True := by\n  trivial",
            "theorem main : IsCorrectAnswer answer := by\n  exact missingProof",
            PLACEHOLDER_FINAL,
        ),
        transaction(
            "theorem premature_repair_decl : True := by\n  trivial",
            "theorem main : IsCorrectAnswer answer := by\n  rfl",
            "def answer : Nat := by\n  exact missingPrematureValue",
        ),
        transaction(
            "theorem five_eq_five : (5 : Nat) = 5 := by\n  rfl",
            open_body,
        ),
        transaction(
            "theorem final_helper : IsCorrectAnswer 5 := by\n  rfl",
            BODY_FINAL,
            "def answer : Nat := by\n  exact missingFinalValue",
        ),
        transaction(
            "theorem final_helper : IsCorrectAnswer 5 := by\n  rfl",
            BODY_FINAL,
            PLACEHOLDER_FINAL,
        ),
    ]

    state = run_problem(
        str(problem),
        runs_root=str(runs),
        reasoning_offline=list(reasoning),
        translation_offline=list(translations),
        reviewer_offline=[ALIGNED] * 20,
    )
    run = runs / "hard_flow"

    check("Hard flow reaches final success", state.status == "final_success")
    check("Hard flow accepts three proof steps",
          state.stats.accepted_proof_steps == 3)
    check("state records Hard Mode", state.problem_mode == "hard")
    check("state commits only the successful placeholder",
          state.placeholder_status == "filled" and
          state.placeholder_final_source == PLACEHOLDER_FINAL)

    failed_candidate = (
        run / "artifacts" / "proof_steps" / "proof_step_002" /
        "informal_candidate_001"
    )
    first_failed_compile = json.loads(
        (failed_candidate / "lean4_attempt_001" / "compile.json").read_text()
    )
    second_failed_compile = json.loads(
        (failed_candidate / "lean4_attempt_002" / "compile.json").read_text()
    )
    check("premature final attempts use Hard final protocol",
          first_failed_compile["attempt_kind"] == "hard_finalization" and
          second_failed_compile["attempt_kind"] == "hard_finalization")
    check("premature final transaction is rejected",
          first_failed_compile["attempt_status"] == "body_failed")
    check("placeholder compiler failure is classified",
          second_failed_compile["attempt_status"] == "placeholder_fill_failed")

    retry_reasoning_prompt = (
        run / "artifacts" / "proof_steps" / "proof_step_002" /
        "informal_candidate_002" / "reasoning_prompt.md"
    ).read_text()
    check("decomposition retry retains only failed NEXT STEP",
          failed_final_step in retry_reasoning_prompt and
          failed_final_proof not in retry_reasoning_prompt and
          failed_final_answer not in retry_reasoning_prompt)
    check("decomposition retry uses normal feedback",
          "Do not repeat it unchanged." in retry_reasoning_prompt)

    later_exploration_prompt = (
        run / "artifacts" / "proof_steps" / "proof_step_002" /
        "informal_candidate_002" / "lean4_attempt_001" /
        "translator_prompt.md"
    ).read_text()
    check("failed final declarations do not leak into later context",
          "premature_decl" not in later_exploration_prompt and
          "premature_repair_decl" not in later_exploration_prompt)
    check("latest accepted body rolls back after failed finalization",
          BODY_INITIAL.strip() in later_exploration_prompt and
          "missingProof" not in later_exploration_prompt)
    check("Hard exploration keeps original placeholder immutable",
          PLACEHOLDER_INITIAL.strip() in later_exploration_prompt and
          "FILLED PLACEHOLDER:\n```lean4" not in later_exploration_prompt)

    accepted_step_2 = run / "accepted" / "proof_step_002"
    check("failed final declarations absent from accepted artifacts",
          "premature" not in
          (accepted_step_2 / "declarations.lean").read_text())
    check("exploration accepted artifacts have no placeholder",
          not (accepted_step_2 / "placeholder.lean").exists())

    final_retry_dir = (
        run / "artifacts" / "proof_steps" / "proof_step_003" /
        "informal_candidate_001"
    )
    rejected_final_raw = (
        final_retry_dir / "lean4_attempt_001" / "raw_translator_output.md"
    ).read_text()
    final_repair_prompt = (
        final_retry_dir / "lean4_attempt_002" / "translator_prompt.md"
    ).read_text()
    check("final repair contains rejected transaction exactly once",
          final_repair_prompt.count(rejected_final_raw) == 1)
    check("final repair identifies placeholder diagnostic",
          "Source region: **rejected FILLED PLACEHOLDER**"
          in final_repair_prompt)

    accepted_final = run / "accepted" / "proof_step_003"
    final_dir = run / "final"
    check("successful final step atomically stores placeholder",
          (accepted_final / "placeholder.lean").read_text() ==
          PLACEHOLDER_FINAL)
    check("final output contains all authoritative regions",
          all((final_dir / name).exists() for name in (
              "solution.lean", "solution.md", "placeholder.lean", "body.lean"
          )))
    final_source = (final_dir / "solution.lean").read_text()
    check("final source has authoritative order",
          final_source.index("theorem final_helper") <
          final_source.index("def answer") <
          final_source.index("theorem main"))
    check("final source contains no sorry", "sorry" not in final_source)

    saved = StateManager.load(str(run))
    final_attempt = (
        saved.proof_steps[2].informal_candidates[0].lean_translation_attempts[1]
    )
    check("saved final attempt records kind and proposed answer",
          final_attempt.attempt_kind == "hard_finalization" and
          final_attempt.proposed_final_answer == "5")

    # A separate exhausted finalization proves that a failed transaction
    # produces no authoritative final/accepted placeholder artifacts.
    failed_problem = make_problem(
        tmp, "hard_failure", candidates=1, translations=1
    )
    failed_state = run_problem(
        str(failed_problem),
        runs_root=str(runs),
        reasoning_offline=[
            action(
                "Finish immediately with five.",
                "Use the proposed answer.",
                final=True,
                answer="5",
            )
        ],
        translation_offline=[
            transaction(
                "theorem rejected_final_decl : True := by\n  trivial",
                "theorem main : IsCorrectAnswer answer := by\n  exact missing",
                PLACEHOLDER_FINAL,
            )
        ],
        reviewer_offline=[ALIGNED] * 5,
    )
    failed_run = runs / "hard_failure"
    check("exhausted finalization follows ordinary step failure",
          failed_state.status == "failed" and
          failed_state.failure_reason == "proof_step_failed")
    check("failed-only run leaves placeholder unresolved",
          failed_state.placeholder_status == "unresolved" and
          failed_state.placeholder_final_source is None)
    check("failed-only run writes no authoritative final files",
          not (failed_run / "final" / "solution.lean").exists() and
          not (failed_run / "final" / "placeholder.lean").exists())
    check("failed-only run accepts no transaction artifacts",
          not any((failed_run / "accepted").rglob("*.lean")))
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print(f"{'check':<64}{'result':<8}detail")
print("-" * 110)
all_ok = True
for label, ok, detail in rows:
    all_ok &= ok
    print(f"{label:<64}{'PASS' if ok else 'FAIL':<8}{detail}")
print("-" * 110)
print(f"\nHARD MODE LOOP: {'PASS' if all_ok else 'FAIL'}")
sys.exit(0 if all_ok else 1)
