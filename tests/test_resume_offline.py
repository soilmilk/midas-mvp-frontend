#!/usr/bin/env python3
"""Deterministic end-to-end coverage for automatic run resume."""
from __future__ import annotations

import glob
import json
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from midas.loop import resume_problem, run_problem


WORK = os.path.join(ROOT, ".test_resume_runs")
PROBLEMS = os.path.join(WORK, "problems")
RUNS = os.path.join(WORK, "runs")
shutil.rmtree(WORK, ignore_errors=True)
os.makedirs(PROBLEMS)


def make_problem(name):
    destination = os.path.join(PROBLEMS, name)
    shutil.copytree(os.path.join(ROOT, "problems", "toy"), destination)
    config_path = os.path.join(destination, "config.json")
    config = json.load(open(config_path))
    config["max_lean_translation_attempts_per_candidate"] = 2
    with open(config_path, "w") as f:
        json.dump(config, f)
    return destination


def R(next_step, proof, *, final=False):
    return (
        f"NEXT STEP:\n{next_step}\n\n"
        f"PROOF:\n{proof}\n\n"
        "STEP USEFULNESS:\nHigh\n\n"
        f"IS_FINAL_STEP: {'True' if final else 'False'}\n\n"
        f"IDEAS FOR THE FUTURE:\n"
        f"{'None — the theorem is complete' if final else '[High] Finish next.'}"
    )


def T(declarations, body, *, final=False):
    heading = "FINAL THEOREM BODY" if final else "UPDATED THEOREM BODY"
    return (
        "INTERMEDIATE REASONING:\nTranslate directly.\n\n"
        "PLAN:\nUse a small declaration.\n\n"
        f"NEW DECLARATIONS:\n```lean4\n{declarations}\n```\n\n"
        f"{heading}:\n```lean4\n{body}\n```\n"
    )


checks = []

# Translator interruption: attempt 1 is a useful compiler failure and attempt 2
# is the earliest provider failure in the terminal step.
translator_problem = make_problem("resume_translator")
failed = run_problem(
    translator_problem,
    runs_root=RUNS,
    reasoning_offline=[
        R("Show A = 5.", "Compute A."),
        RuntimeError("later reasoner outage"),
        RuntimeError("later reasoner outage"),
    ],
    translation_offline=[
        T("theorem broken_resume : A = 5 := by exact missing_name",
          "theorem main : f A = f B := by\n  sorry"),
        RuntimeError("translator outage"),
    ],
)
translator_root = os.path.join(RUNS, "resume_translator")
attempt1 = os.path.join(
    translator_root, "artifacts", "proof_steps", "proof_step_001",
    "informal_candidate_001", "lean4_attempt_001", "compile.json",
)
attempt1_before = open(attempt1).read()
failed_attempts = failed.stats.total_lean_attempts
failed_calls = failed.stats.total_llm_calls
failed_runtime = failed.stats.runtime_seconds
checks.append(("translator fixture fails", failed.status == "failed"))

resumed = resume_problem(
    translator_root,
    reasoning_offline=[R("Finish the theorem.", "Compute both sides.", final=True)],
    translation_offline=[
        T("theorem A_eq_resume : A = 5 := by decide",
          "theorem main : f A = f B := by\n  sorry"),
        T("theorem finished_resume : f A = f B := by decide",
          "theorem main : f A = f B := by\n  exact finished_resume",
          final=True),
    ],
)
archives = glob.glob(os.path.join(translator_root, "archive", "resume_*"))
archive = archives[0] if len(archives) == 1 else ""
checks.extend([
    ("translator resume succeeds", resumed.status == "final_success"),
    ("accepted prefix attempt is unchanged", open(attempt1).read() == attempt1_before),
    ("one archive is created", len(archives) == 1),
    ("original state is archived", bool(archive) and os.path.isfile(
        os.path.join(archive, "state.json"))),
    ("failed target attempt is archived", bool(archive) and os.path.isfile(
        os.path.join(archive, "artifacts", "proof_steps", "proof_step_001",
                     "informal_candidate_001", "lean4_attempt_002", "compile.json"))),
    ("later candidates are archived", bool(archive) and os.path.isdir(
        os.path.join(archive, "artifacts", "proof_steps", "proof_step_001",
                     "informal_candidate_002"))),
    ("failure report is archived", bool(archive) and os.path.isfile(
        os.path.join(archive, "failure", "failure_report.md"))),
    ("usage counters are cumulative",
     resumed.stats.total_llm_calls > failed_calls
     and resumed.stats.total_lean_attempts > failed_attempts),
    ("runtime is cumulative", resumed.stats.runtime_seconds >= failed_runtime),
    ("resume log is appended", "Resume started: resume_translator" in open(
        os.path.join(translator_root, "log.txt")).read()),
])

# Reasoner interruption: with no earlier translator outage, candidate 1 is
# regenerated automatically and can finish directly.
reasoner_problem = make_problem("resume_reasoner")
reasoner_failed = run_problem(
    reasoner_problem,
    runs_root=RUNS,
    reasoning_offline=[RuntimeError("reasoner outage")] * 3,
    translation_offline=[],
)
reasoner_root = os.path.join(RUNS, "resume_reasoner")
reasoner_resumed = resume_problem(
    reasoner_root,
    reasoning_offline=[R("Finish directly.", "Both sides compute.", final=True)],
    translation_offline=[
        T("theorem direct_resume : f A = f B := by decide",
          "theorem main : f A = f B := by\n  exact direct_resume",
          final=True),
    ],
)
reasoner_archives = glob.glob(os.path.join(reasoner_root, "archive", "resume_*"))
checks.extend([
    ("reasoner fixture fails", reasoner_failed.status == "failed"),
    ("reasoner resume succeeds", reasoner_resumed.status == "final_success"),
    ("reasoner suffix is archived", len(reasoner_archives) == 1 and os.path.isdir(
        os.path.join(reasoner_archives[0], "artifacts", "proof_steps",
                     "proof_step_001", "informal_candidate_001"))),
])

# Successful runs are never mutated by resume.
try:
    resume_problem(reasoner_root, reasoning_offline=[], translation_offline=[])
    successful_rejected = False
except ValueError as error:
    successful_rejected = "successful run" in str(error)
checks.append(("successful run is rejected", successful_rejected))

print(f"{'check':<46} result")
print("-" * 54)
for label, ok in checks:
    print(f"{label:<46} {'PASS' if ok else 'FAIL'}")

ok = all(value for _, value in checks)
print(f"\nRESUME OFFLINE: {'PASS' if ok else 'FAIL'}")
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(0 if ok else 1)
