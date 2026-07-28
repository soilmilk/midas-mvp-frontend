#!/usr/bin/env python3
"""Phase 4 persistence, diagnostics, CLI, replay, and legacy-compatibility gate."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from midas.loop import run_problem


CONTEXT = "def IsCorrectAnswer (n : Nat) : Prop := n = 5\n"
PLACEHOLDER_INITIAL = "def answer : Nat := by\n  sorry\n"
PLACEHOLDER_BAD = "def answer : Nat := by\n  exact True"
PLACEHOLDER_FINAL = "def answer : Nat := by\n  exact 5"
BODY_INITIAL = "theorem main : IsCorrectAnswer answer := by\n  sorry\n"
BODY_FINAL = "theorem main : IsCorrectAnswer answer := by\n  exact five_is_correct"


def action(next_step: str, *, final=False, answer=None) -> str:
    raw = (
        f"NEXT STEP:\n{next_step}\n\n"
        "PROOF:\nThe claim follows directly from the definitions.\n\n"
        f"IS_FINAL_STEP: {'True' if final else 'False'}"
    )
    if answer is not None:
        raw += f"\n\nANSWER:\n{answer}"
    return raw


def transaction(declarations: str, body: str, placeholder=None) -> str:
    sections = [f"NEW DECLARATIONS:\n```lean4\n{declarations}\n```"]
    if placeholder is not None:
        sections.append(
            f"FILLED PLACEHOLDER:\n```lean4\n{placeholder}\n```"
        )
        heading = "FINAL THEOREM BODY"
    else:
        heading = "UPDATED THEOREM BODY"
    sections.append(f"{heading}:\n```lean4\n{body}\n```")
    return "\n\n".join(sections)


def make_hard_problem(root: Path, pid: str, translations=2) -> Path:
    problem = root / pid
    inp = problem / "input"
    inp.mkdir(parents=True)
    (problem / "config.json").write_text(json.dumps({
        "problem_mode": "hard",
        "max_proof_steps": 3,
        "max_informal_candidates_per_proof_step": 1,
        "max_lean_translation_attempts_per_candidate": translations,
        "max_total_lean_attempts": 10,
        "max_runtime_seconds": 120,
        "lean_prelude": [],
        "reasoning_model": "offline",
        "translation_model": "offline",
    }))
    (inp / "informal_problem.md").write_text("Find and verify the number five.")
    (inp / "context.lean").write_text(CONTEXT)
    (inp / "placeholder.lean").write_text(PLACEHOLDER_INITIAL)
    (inp / "body_initial.lean").write_text(BODY_INITIAL)
    return problem


def make_easy_problem(root: Path, pid: str) -> Path:
    problem = root / pid
    inp = problem / "input"
    inp.mkdir(parents=True)
    (problem / "config.json").write_text(json.dumps({
        "max_proof_steps": 1,
        "max_informal_candidates_per_proof_step": 1,
        "max_lean_translation_attempts_per_candidate": 1,
        "max_total_lean_attempts": 2,
        "max_runtime_seconds": 120,
        "lean_prelude": [],
        "reasoning_model": "offline",
        "translation_model": "offline",
    }))
    (inp / "informal_problem.md").write_text("Prove A equals itself.")
    (inp / "context.lean").write_text("def A : Nat := 5\n")
    (inp / "body_initial.lean").write_text(
        "theorem main : A = A := by\n  sorry\n"
    )
    return problem


def cli(runs: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "midas.cli",
            "--runs-root",
            str(runs),
            *map(str, args),
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )


def make_legacy_easy_run(runs: Path):
    root = runs / "legacy_easy"
    attempt = (
        root / "artifacts" / "proof_steps" / "proof_step_001" /
        "informal_candidate_001" / "lean4_attempt_001"
    )
    accepted = root / "accepted" / "proof_step_001"
    inp = root / "input"
    attempt.mkdir(parents=True)
    accepted.mkdir(parents=True)
    inp.mkdir(parents=True)
    context = "def A : Nat := 5\n"
    body = "theorem main : A = A := by\n  rfl\n"
    (root / "config.json").write_text(json.dumps({"lean_prelude": []}))
    (inp / "context.lean").write_text(context)
    (inp / "body_initial.lean").write_text(body)
    (inp / "informal_problem.md").write_text("Prove A equals itself.")
    (attempt / "declarations.lean").write_text("")
    (attempt / "body.lean").write_text(body)
    (attempt / "compile.json").write_text(json.dumps({
        "attempt_status": "accepted",
        "structure_check": {"status": "passed", "errors": []},
        "declaration_check": {"status": "passed", "errors": []},
        "body_check": {"status": "passed", "errors": []},
        "final_check": {"status": "not_run", "errors": []},
    }))
    (accepted / "declarations.lean").write_text("")
    (accepted / "body.lean").write_text(body)
    (root / "state.json").write_text(json.dumps({
        "problem_id": "legacy_easy",
        "informal_problem_path": "input/informal_problem.md",
        "context_path": "input/context.lean",
        "initial_body_path": "input/body_initial.lean",
        "formal_theorem_header": "theorem main : A = A := by",
        "status": "running",
        "stats": {},
        "proof_steps": [{
            "proof_step_index": 1,
            "status": "accepted",
            "informal_candidates": [{
                "informal_candidate_index": 1,
                "status": "accepted",
                "lean_translation_attempts": [{
                    "lean_translation_attempt_index": 1,
                    "status": "accepted",
                }],
            }],
        }],
    }))


rows = []


def check(label, condition, detail=""):
    rows.append((label, bool(condition), detail))


tmp = Path(tempfile.mkdtemp(prefix="midas-hard-cli-"))
try:
    runs = tmp / "runs"
    problem = make_hard_problem(tmp, "hard_cli")
    state = run_problem(
        str(problem),
        runs_root=str(runs),
        reasoning_offline=[
            action("Show that five satisfies the predicate."),
            action("Fill the answer with five and finish.", final=True, answer="5"),
        ],
        translation_offline=[
            transaction(
                "theorem five_is_correct : IsCorrectAnswer 5 := by\n  rfl",
                BODY_INITIAL.strip(),
            ),
            transaction("", BODY_FINAL, PLACEHOLDER_BAD),
            transaction("", BODY_FINAL, PLACEHOLDER_FINAL),
        ],
    )
    run = runs / "hard_cli"
    check("Phase 4 fixture reaches final success", state.status == "final_success")
    check("temporary final proposal is not retained",
          not (run / "tmp" / "final_candidate.lean").exists())
    check("only Hard finalization attempts store candidate placeholders",
          not (
              run / "artifacts" / "proof_steps" / "proof_step_001" /
              "informal_candidate_001" / "lean4_attempt_001" /
              "placeholder.lean"
          ).exists() and all((
              run / "artifacts" / "proof_steps" / "proof_step_002" /
              "informal_candidate_001" / f"lean4_attempt_{index:03d}" /
              "placeholder.lean"
          ).exists() for index in (1, 2)))

    status = cli(runs, "status", "hard_cli")
    check("status succeeds", status.returncode == 0, status.stderr)
    check("status displays problem mode", "mode    : hard" in status.stdout)
    check("status displays placeholder status",
          "placeholder status : filled" in status.stdout)
    check("status displays placeholder name and header",
          "placeholder name   : answer" in status.stdout and
          "def answer : Nat := by" in status.stdout)

    attempts = cli(runs, "attempts", "hard_cli")
    check("attempts succeeds", attempts.returncode == 0, attempts.stderr)
    check("attempts displays both attempt kinds",
          "exploration" in attempts.stdout and
          "hard_finalization" in attempts.stdout)
    check("attempts displays placeholder presence",
          "placeholder?" in attempts.stdout and
          attempts.stdout.count("yes") >= 2)
    failed_attempts = cli(runs, "attempts", "hard_cli", "--step", 2, "--failed-only")
    check("attempt filters remain functional",
          failed_attempts.returncode == 0 and
          "placeholder_fill_failed" in failed_attempts.stdout and
          "final_success" not in failed_attempts.stdout)

    show = cli(runs, "show", "hard_cli", 2, 1, 2)
    check("show succeeds", show.returncode == 0, show.stderr)
    check("show displays parsed placeholder",
          "PARSED placeholder.lean" in show.stdout and
          PLACEHOLDER_FINAL in show.stdout)
    check("show preserves source-region display order",
          show.stdout.index("PARSED declarations.lean") <
          show.stdout.index("PARSED placeholder.lean") <
          show.stdout.index("PARSED body.lean"))

    exploration_replay = cli(runs, "replay", "hard_cli", 1, 1, 1)
    check("exploration replay succeeds",
          exploration_replay.returncode == 0, exploration_replay.stderr)
    check("exploration replay uses original placeholder protocol",
          "kind=exploration" in exploration_replay.stdout and
          "=> ACCEPTED (would advance)" in exploration_replay.stdout and
          "verdict_match     : yes" in exploration_replay.stdout)

    failed_final_replay = cli(runs, "replay", "hard_cli", 2, 1, 1)
    check("failed final replay succeeds as a diagnostic command",
          failed_final_replay.returncode == 0, failed_final_replay.stderr)
    check("failed final replay reproduces rejection",
          "kind=hard_finalization" in failed_final_replay.stdout and
          "=> REJECTED" in failed_final_replay.stdout and
          "verdict_match     : yes" in failed_final_replay.stdout)

    final_replay = cli(runs, "replay", "hard_cli", 2, 1, 2)
    check("successful final replay succeeds",
          final_replay.returncode == 0, final_replay.stderr)
    check("final replay repeats independent full-file check",
          "final_check      : passed" in final_replay.stdout and
          "verdict_match     : yes" in final_replay.stdout)

    failed_compile = json.loads((
        run / "artifacts" / "proof_steps" / "proof_step_002" /
        "informal_candidate_001" / "lean4_attempt_001" / "compile.json"
    ).read_text())
    failed_error = failed_compile["body_check"]["errors"][0]
    check("compile report persists placeholder region",
          failed_error["source_region"] == "rejected FILLED PLACEHOLDER",
          failed_error.get("source_region", ""))
    check("compile report persists numbered nearby code",
          "exact True" in failed_error["nearby_code"],
          failed_error.get("nearby_code", ""))

    parse_problem = make_hard_problem(tmp, "hard_parse", translations=1)
    run_problem(
        str(parse_problem),
        runs_root=str(runs),
        reasoning_offline=[
            action("Finish with five.", final=True, answer="5"),
        ],
        translation_offline=["not a parsed translator transaction"],
    )
    parse_replay = cli(runs, "replay", "hard_parse", 1, 1, 1)
    check("parse-error replay fails in a controlled way",
          parse_replay.returncode != 0 and
          "not replayable" in (parse_replay.stdout + parse_replay.stderr))
    parse_show = cli(runs, "show", "hard_parse", 1, 1, 1)
    check("parse-error attempt remains inspectable with show",
          parse_show.returncode == 0 and
          "not a parsed translator transaction" in parse_show.stdout)
    check("Hard failure artifact retains unresolved placeholder",
          PLACEHOLDER_INITIAL.strip() in (
              runs / "hard_parse" / "failure" / "last_verified.lean"
          ).read_text())

    easy_problem = make_easy_problem(tmp, "easy_cli")
    easy_state = run_problem(
        str(easy_problem),
        runs_root=str(runs),
        reasoning_offline=[
            action("Finish by reflexivity.", final=True),
        ],
        translation_offline=[
            (
                "NEW DECLARATIONS:\n```lean4\n\n```\n\n"
                "FINAL THEOREM BODY:\n```lean4\n"
                "theorem main : A = A := by\n  rfl\n```"
            ),
        ],
    )
    check("Easy finalization fixture reaches success",
          easy_state.status == "final_success")
    easy_show = cli(runs, "show", "easy_cli", 1, 1, 1)
    check("Easy show consistently omits placeholder section",
          easy_show.returncode == 0 and
          "PARSED placeholder.lean" not in easy_show.stdout)
    easy_replay = cli(runs, "replay", "easy_cli", 1, 1, 1)
    check("Easy finalization replay remains compatible",
          easy_replay.returncode == 0 and
          "kind=easy_finalization" in easy_replay.stdout and
          "final_check      : passed" in easy_replay.stdout and
          "verdict_match     : yes" in easy_replay.stdout)

    make_legacy_easy_run(runs)
    legacy_status = cli(runs, "status", "legacy_easy")
    check("legacy state without Phase 4 fields loads",
          legacy_status.returncode == 0 and
          "mode    : easy" in legacy_status.stdout)
    legacy_show = cli(runs, "show", "legacy_easy", 1, 1, 1)
    check("legacy attempt without attempt kind remains showable",
          legacy_show.returncode == 0 and
          "PARSED body.lean" in legacy_show.stdout)
    legacy_replay = cli(runs, "replay", "legacy_easy", 1, 1, 1)
    check("legacy exploration artifact replays",
          legacy_replay.returncode == 0 and
          "kind=exploration" in legacy_replay.stdout and
          "verdict_match     : yes" in legacy_replay.stdout)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print(f"{'check':<66}{'result':<8}detail")
print("-" * 112)
all_ok = True
for label, ok, detail in rows:
    all_ok &= ok
    print(f"{label:<66}{'PASS' if ok else 'FAIL':<8}{detail}")
print("-" * 112)
print(f"\nHARD MODE PHASE 4 CLI: {'PASS' if all_ok else 'FAIL'}")
sys.exit(0 if all_ok else 1)
