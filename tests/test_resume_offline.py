#!/usr/bin/env python3
"""Deterministic end-to-end coverage for automatic run resume."""
from __future__ import annotations

import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from midas.loop import custom_resume_problem, resume_problem, run_problem


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

ALIGNED = (
    "INTERMEDIATE REASONING:\nThe transaction establishes the requested step.\n\n"
    "VERDICT: ALIGNED\n\nFEEDBACK:\nNone"
)


checks = []


def tree_digest(root):
    digest = hashlib.sha256()
    for directory, _, filenames in os.walk(root):
        for filename in sorted(filenames):
            path = os.path.join(directory, filename)
            digest.update(os.path.relpath(path, root).encode())
            digest.update(open(path, "rb").read())
    return digest.hexdigest()

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
    reviewer_offline=[ALIGNED] * 10,
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

# Simulate a run created by the previous protocol. Resume must tolerate both the
# retired state metadata and its saved reasoner action without reintroducing the
# field for new calls or newly serialized state.
legacy_candidate = os.path.join(
    translator_root, "artifacts", "proof_steps", "proof_step_001",
    "informal_candidate_001", "informal_step.md",
)
legacy_text = open(legacy_candidate).read().replace(
    "IS_FINAL_STEP: False",
    "STEP USEFULNESS:\nHigh\n\nIS_FINAL_STEP: False",
)
with open(legacy_candidate, "w") as f:
    f.write(legacy_text)
legacy_state_path = os.path.join(translator_root, "state.json")
legacy_state = json.load(open(legacy_state_path))
legacy_state["current_knowledge"] = [
    {"statement": item["statement"], "step_usefulness": "High"}
    for item in legacy_state["current_knowledge"]
]
legacy_state["proof_steps"][0]["informal_candidates"][0]["step_usefulness"] = "High"
with open(legacy_state_path, "w") as f:
    json.dump(legacy_state, f)

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
    reviewer_offline=[ALIGNED] * 10,
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
    ("resaved state drops legacy usefulness metadata",
     "step_usefulness" not in open(legacy_state_path).read()),
])

# Reasoner interruption: with no earlier translator outage, candidate 1 is
# regenerated automatically and can finish directly.
reasoner_problem = make_problem("resume_reasoner")
reasoner_failed = run_problem(
    reasoner_problem,
    runs_root=RUNS,
    reasoning_offline=[RuntimeError("reasoner outage")] * 3,
    translation_offline=[],
    reviewer_offline=[],
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
    reviewer_offline=[ALIGNED] * 5,
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
    resume_problem(reasoner_root, reasoning_offline=[], translation_offline=[],
                   reviewer_offline=[])
    successful_rejected = False
except ValueError as error:
    successful_rejected = "successful run" in str(error)
checks.append(("successful run is rejected", successful_rejected))

# Custom resume may branch a successful run at an exact historical reasoner call.
source_digest = tree_digest(reasoner_root)
reasoner_branch_root = os.path.join(RUNS, "custom_reasoner_branch")
reasoner_branch = custom_resume_problem(
    reasoner_root,
    reasoner_branch_root,
    step=1,
    candidate=1,
    stage="reasoner",
    reasoning_offline=[R("Finish on the branch.", "Both sides compute.", final=True)],
    translation_offline=[
        T("theorem branch_finish : f A = f B := by decide",
          "theorem main : f A = f B := by\n  exact branch_finish",
          final=True),
    ],
    reviewer_offline=[ALIGNED],
)
branch_state_text = open(os.path.join(reasoner_branch_root, "state.json")).read()
checks.extend([
    ("custom reasoner branch succeeds", reasoner_branch.status == "final_success"),
    ("custom branch leaves source byte-identical", tree_digest(reasoner_root) == source_digest),
    ("custom branch records provenance", os.path.isfile(
        os.path.join(reasoner_branch_root, "branch.json"))),
    ("custom branch rebases persisted paths",
     reasoner_root + os.sep not in branch_state_text and
     reasoner_branch_root + os.sep in branch_state_text),
    ("custom branch omits discarded archive", not os.path.exists(
        os.path.join(reasoner_branch_root, "archive"))),
])

translator_branch_root = os.path.join(RUNS, "custom_translator_branch")
translator_branch = custom_resume_problem(
    translator_root,
    translator_branch_root,
    step=1,
    candidate=1,
    stage="translator",
    attempt=2,
    reasoning_offline=[R("Finish the translator branch.", "Compute.", final=True)],
    translation_offline=[
        T("theorem A_eq_branch : A = 5 := by decide",
          "theorem main : f A = f B := by\n  sorry"),
        T("theorem translator_branch_done : f A = f B := by decide",
          "theorem main : f A = f B := by\n  exact translator_branch_done",
          final=True),
    ],
    reviewer_offline=[ALIGNED, ALIGNED],
)
checks.extend([
    ("custom translator branch succeeds", translator_branch.status == "final_success"),
    ("custom translator branch retains earlier attempt", os.path.isfile(os.path.join(
        translator_branch_root, "artifacts", "proof_steps", "proof_step_001",
        "informal_candidate_001", "lean4_attempt_001", "compile.json"))),
    ("custom branch recomputes retained-prefix accounting",
     translator_branch.stats.total_llm_calls == 7 and
     translator_branch.stats.total_lean_attempts == 3 and
     translator_branch.stats.llm_usage.total.total_tokens == 0),
])

reviewer_branch_root = os.path.join(RUNS, "custom_reviewer_branch")
reviewer_branch = custom_resume_problem(
    translator_root,
    reviewer_branch_root,
    step=1,
    candidate=1,
    stage="reviewer",
    attempt=2,
    reviewer_attempt=1,
    reasoning_offline=[R("Finish the reviewer branch.", "Compute.", final=True)],
    translation_offline=[
        T("theorem reviewer_branch_done : f A = f B := by decide",
          "theorem main : f A = f B := by\n  exact reviewer_branch_done",
          final=True),
    ],
    reviewer_offline=[ALIGNED, ALIGNED],
)
checks.extend([
    ("custom reviewer branch succeeds", reviewer_branch.status == "final_success"),
    ("custom reviewer branch records exact index",
     json.load(open(os.path.join(reviewer_branch_root, "branch.json")))
     ["checkpoint"]["reviewer_attempt"] == 1),
])

try:
    custom_resume_problem(
        reasoner_root,
        reasoner_branch_root,
        step=1,
        candidate=1,
        stage="reasoner",
        reasoning_offline=[],
        translation_offline=[],
        reviewer_offline=[],
    )
    collision_rejected = False
except FileExistsError:
    collision_rejected = True
checks.append(("custom branch destination collision is rejected", collision_rejected))

status_result = subprocess.run(
    [sys.executable, "-m", "midas.cli", "--runs-root", RUNS,
     "status", "custom_reasoner_branch"],
    cwd=ROOT,
    text=True,
    capture_output=True,
)
collision_result = subprocess.run(
    [sys.executable, "-m", "midas.cli", "--runs-root", RUNS,
     "custom-resume", "resume_reasoner", "custom_reasoner_branch",
     "--step", "1", "--candidate", "1", "--stage", "reasoner"],
    cwd=ROOT,
    text=True,
    capture_output=True,
)
checks.extend([
    ("status displays custom branch provenance",
     status_result.returncode == 0 and "checkpoint:" in status_result.stdout and
     "stage=reasoner" in status_result.stdout),
    ("custom-resume CLI reports destination collision",
     collision_result.returncode != 0 and
     "Run folder already exists" in collision_result.stderr),
])

invalid_destination = os.path.join(RUNS, "invalid_custom_branch")
try:
    custom_resume_problem(
        reasoner_root,
        invalid_destination,
        step=1,
        candidate=1,
        stage="translator",
        reasoning_offline=[],
        translation_offline=[],
        reviewer_offline=[],
    )
    invalid_selector_rejected = False
except ValueError as error:
    invalid_selector_rejected = "requires attempt" in str(error)
checks.append(("custom branch rejects invalid selector combinations",
               invalid_selector_rejected and not os.path.exists(invalid_destination)))

# A clean runtime cutoff after a completed compiler failure has no interrupted
# call to retry. Resume must retain that attempt and start the next one.
runtime_attempt_problem = make_problem("runtime_next_attempt")
runtime_attempt_config_path = os.path.join(runtime_attempt_problem, "config.json")
runtime_attempt_config = json.load(open(runtime_attempt_config_path))
runtime_attempt_config["max_informal_candidates_per_proof_step"] = 1
runtime_attempt_config["max_lean_translation_attempts_per_candidate"] = 1
with open(runtime_attempt_config_path, "w") as f:
    json.dump(runtime_attempt_config, f)
runtime_attempt_failed = run_problem(
    runtime_attempt_problem,
    runs_root=RUNS,
    reasoning_offline=[R("Show A = 5.", "Compute A.")],
    translation_offline=[
        T("theorem broken_runtime : A = 5 := by exact missing_name",
          "theorem main : f A = f B := by\n  sorry"),
    ],
    reviewer_offline=[],
)
runtime_attempt_root = os.path.join(RUNS, "runtime_next_attempt")
runtime_attempt_one = os.path.join(
    runtime_attempt_root, "artifacts", "proof_steps", "proof_step_001",
    "informal_candidate_001", "lean4_attempt_001", "compile.json",
)
runtime_attempt_one_before = open(runtime_attempt_one).read()
runtime_attempt_state_path = os.path.join(runtime_attempt_root, "state.json")
runtime_attempt_state = json.load(open(runtime_attempt_state_path))
runtime_attempt_state["status"] = "failed"
runtime_attempt_state["failure_reason"] = "max_runtime_seconds"
runtime_attempt_state["proof_steps"][-1]["status"] = "pending"
runtime_attempt_state["proof_steps"][-1]["informal_candidates"][-1]["status"] = "pending"
with open(runtime_attempt_state_path, "w") as f:
    json.dump(runtime_attempt_state, f)
runtime_saved_config_path = os.path.join(runtime_attempt_root, "config.json")
runtime_saved_config = json.load(open(runtime_saved_config_path))
runtime_saved_config["max_lean_translation_attempts_per_candidate"] = 2
with open(runtime_saved_config_path, "w") as f:
    json.dump(runtime_saved_config, f)
runtime_reasoner_calls_before = runtime_attempt_failed.stats.llm_usage.reasoner.calls
runtime_attempt_resumed = resume_problem(
    runtime_attempt_root,
    reasoning_offline=[R("Finish the theorem.", "Use A = 5.", final=True)],
    translation_offline=[
        T("theorem A_eq_runtime : A = 5 := by decide",
          "theorem main : f A = f B := by\n  sorry"),
        T("theorem runtime_finish : f A = f B := by decide",
          "theorem main : f A = f B := by\n  exact runtime_finish",
          final=True),
    ],
    reviewer_offline=[ALIGNED, ALIGNED],
)
runtime_attempt_two_prompt = os.path.join(
    runtime_attempt_root, "artifacts", "proof_steps", "proof_step_001",
    "informal_candidate_001", "lean4_attempt_002", "translator_prompt.md",
)
checks.extend([
    ("runtime resume continues at next translator attempt",
     runtime_attempt_resumed.status == "final_success" and
     open(runtime_attempt_one).read() == runtime_attempt_one_before and
     "missing_name" in open(runtime_attempt_two_prompt).read()),
    ("runtime translator continuation does not replay reasoner",
     runtime_attempt_resumed.stats.llm_usage.reasoner.calls ==
     runtime_reasoner_calls_before + 1),
])

# If runtime expires at semantic review, keep the compiling translation and
# retry only the reviewer transaction.
runtime_reviewer_problem = make_problem("runtime_reviewer")
runtime_reviewer_config_path = os.path.join(runtime_reviewer_problem, "config.json")
runtime_reviewer_config = json.load(open(runtime_reviewer_config_path))
runtime_reviewer_config["max_informal_candidates_per_proof_step"] = 1
runtime_reviewer_config["max_lean_translation_attempts_per_candidate"] = 1
runtime_reviewer_config["max_reviewer_call_attempts"] = 1
with open(runtime_reviewer_config_path, "w") as f:
    json.dump(runtime_reviewer_config, f)
runtime_reviewer_failed = run_problem(
    runtime_reviewer_problem,
    runs_root=RUNS,
    reasoning_offline=[R("Finish directly.", "Compute both sides.", final=True)],
    translation_offline=[
        T("theorem reviewer_runtime_finish : f A = f B := by decide",
          "theorem main : f A = f B := by\n  exact reviewer_runtime_finish",
          final=True),
    ],
    reviewer_offline=[RuntimeError("reviewer deadline")],
)
runtime_reviewer_root = os.path.join(RUNS, "runtime_reviewer")
runtime_reviewer_state_path = os.path.join(runtime_reviewer_root, "state.json")
runtime_reviewer_state = json.load(open(runtime_reviewer_state_path))
runtime_reviewer_state["status"] = "failed"
runtime_reviewer_state["failure_reason"] = "max_runtime_seconds"
runtime_reviewer_state["proof_steps"][-1]["status"] = "pending"
runtime_reviewer_state["proof_steps"][-1]["informal_candidates"][-1]["status"] = "pending"
with open(runtime_reviewer_state_path, "w") as f:
    json.dump(runtime_reviewer_state, f)
runtime_reviewer_resumed = resume_problem(
    runtime_reviewer_root,
    reasoning_offline=[],
    translation_offline=[],
    reviewer_offline=[ALIGNED],
)
checks.append((
    "runtime reviewer continuation reuses translator output",
    runtime_reviewer_resumed.status == "final_success" and
    runtime_reviewer_resumed.stats.total_lean_attempts ==
    runtime_reviewer_failed.stats.total_lean_attempts and
    runtime_reviewer_resumed.stats.llm_usage.translator.calls ==
    runtime_reviewer_failed.stats.llm_usage.translator.calls,
))

# When the active candidate has exhausted its configured attempts, a larger
# saved candidate limit lets runtime resume begin the next candidate.
runtime_candidate_problem = make_problem("runtime_next_candidate")
runtime_candidate_config_path = os.path.join(runtime_candidate_problem, "config.json")
runtime_candidate_config = json.load(open(runtime_candidate_config_path))
runtime_candidate_config["max_informal_candidates_per_proof_step"] = 1
runtime_candidate_config["max_lean_translation_attempts_per_candidate"] = 1
with open(runtime_candidate_config_path, "w") as f:
    json.dump(runtime_candidate_config, f)
run_problem(
    runtime_candidate_problem,
    runs_root=RUNS,
    reasoning_offline=[R("Try a bad step.", "This attempt will fail.")],
    translation_offline=[
        T("theorem broken_candidate : A = 5 := by exact missing_name",
          "theorem main : f A = f B := by\n  sorry"),
    ],
    reviewer_offline=[],
)
runtime_candidate_root = os.path.join(RUNS, "runtime_next_candidate")
runtime_candidate_state_path = os.path.join(runtime_candidate_root, "state.json")
runtime_candidate_state = json.load(open(runtime_candidate_state_path))
runtime_candidate_state["status"] = "failed"
runtime_candidate_state["failure_reason"] = "max_runtime_seconds"
runtime_candidate_state["proof_steps"][-1]["status"] = "pending"
with open(runtime_candidate_state_path, "w") as f:
    json.dump(runtime_candidate_state, f)
runtime_candidate_saved_config_path = os.path.join(runtime_candidate_root, "config.json")
runtime_candidate_saved_config = json.load(open(runtime_candidate_saved_config_path))
runtime_candidate_saved_config["max_informal_candidates_per_proof_step"] = 2
with open(runtime_candidate_saved_config_path, "w") as f:
    json.dump(runtime_candidate_saved_config, f)
runtime_candidate_resumed = resume_problem(
    runtime_candidate_root,
    reasoning_offline=[R("Finish directly.", "Compute both sides.", final=True)],
    translation_offline=[
        T("theorem candidate_finish : f A = f B := by decide",
          "theorem main : f A = f B := by\n  exact candidate_finish",
          final=True),
    ],
    reviewer_offline=[ALIGNED],
)
checks.append((
    "runtime resume can start the next candidate",
    runtime_candidate_resumed.status == "final_success" and
    runtime_candidate_resumed.proof_steps[0].informal_candidates[-1].informal_candidate_index == 2 and
    "informal_candidate_002/new_reasoner_call" in open(
        os.path.join(runtime_candidate_root, "log.txt")
    ).read(),
))

# A cutoff between accepted proof steps has no terminal incomplete step. Resume
# must append the next proof step instead of rewinding the accepted one.
runtime_step_problem = make_problem("runtime_next_step")
runtime_step_config_path = os.path.join(runtime_step_problem, "config.json")
runtime_step_config = json.load(open(runtime_step_config_path))
runtime_step_config["max_informal_candidates_per_proof_step"] = 1
runtime_step_config["max_lean_translation_attempts_per_candidate"] = 1
with open(runtime_step_config_path, "w") as f:
    json.dump(runtime_step_config, f)
run_problem(
    runtime_step_problem,
    runs_root=RUNS,
    reasoning_offline=[
        R("Show A = 5.", "Compute A."),
        RuntimeError("later reasoner outage"),
    ],
    translation_offline=[
        T("theorem A_eq_step_boundary : A = 5 := by decide",
          "theorem main : f A = f B := by\n  sorry"),
    ],
    reviewer_offline=[ALIGNED],
)
runtime_step_root = os.path.join(RUNS, "runtime_next_step")
runtime_step_state_path = os.path.join(runtime_step_root, "state.json")
runtime_step_state = json.load(open(runtime_step_state_path))
runtime_step_state["proof_steps"] = runtime_step_state["proof_steps"][:1]
runtime_step_state["status"] = "failed"
runtime_step_state["failure_reason"] = "max_runtime_seconds"
with open(runtime_step_state_path, "w") as f:
    json.dump(runtime_step_state, f)
shutil.rmtree(os.path.join(
    runtime_step_root, "artifacts", "proof_steps", "proof_step_002"
), ignore_errors=True)
runtime_step_resumed = resume_problem(
    runtime_step_root,
    reasoning_offline=[R("Finish now.", "Use the accepted lemma.", final=True)],
    translation_offline=[
        T("theorem step_boundary_finish : f A = f B := by decide",
          "theorem main : f A = f B := by\n  exact step_boundary_finish",
          final=True),
    ],
    reviewer_offline=[ALIGNED],
)
checks.append((
    "runtime resume can start a new proof step",
    runtime_step_resumed.status == "final_success" and
    len(runtime_step_resumed.proof_steps) == 2 and
    "proof_step_002/new_proof_step" in open(
        os.path.join(runtime_step_root, "log.txt")
    ).read(),
))

print(f"{'check':<46} result")
print("-" * 54)
for label, ok in checks:
    print(f"{label:<46} {'PASS' if ok else 'FAIL'}")

ok = all(value for _, value in checks)
print(f"\nRESUME OFFLINE: {'PASS' if ok else 'FAIL'}")
shutil.rmtree(WORK, ignore_errors=True)
sys.exit(0 if ok else 1)
