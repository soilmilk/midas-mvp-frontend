#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from build_accepted_dataset import DatasetBuildError, build_dataset
from midas.reconstructor import render_source


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_run(
    runs: Path,
    run_id: str,
    *,
    problem_id: str = "shared_problem",
    mode: str = "easy",
    failed: bool = False,
    mismatched_final: bool = False,
) -> Path:
    run = runs / run_id
    prelude = ["import Mathlib"]
    context = "def seed : Nat := 1\n"
    initial_body = "theorem target : True := by\n  sorry\n"
    initial_placeholder = "def answer : Nat := by\n  sorry\n" if mode == "hard" else ""
    _write(run / "config.json", json.dumps({"problem_mode": mode, "lean_prelude": prelude}))
    _write(run / "input" / "context.lean", context)
    _write(run / "input" / "body_initial.lean", initial_body)
    _write(run / "input" / "informal_problem.md", "Prove True.")
    if mode == "hard":
        _write(run / "input" / "placeholder.lean", initial_placeholder)

    declarations = "lemma helper : True := by trivial\n"
    body = "theorem target : True := by\n  exact helper\n"
    filled_placeholder = "def answer : Nat := by\n  exact 1\n" if mode == "hard" else ""
    step_dir = run / "accepted" / "proof_step_001"
    _write(step_dir / "declarations.lean", declarations)
    _write(step_dir / "body.lean", body)
    if mode == "hard":
        _write(step_dir / "placeholder.lean", filled_placeholder)

    candidate_dir = (
        run / "artifacts" / "proof_steps" / "proof_step_001" /
        "informal_candidate_002"
    )
    informal = (
        "INTERMEDIATE REASONING:\nIgnore this.\n\n"
        "NEXT STEP:\nProve the helper and finish.\n\n"
        "PROOF:\nTrue is immediate.\n\n"
        "IS_FINAL_STEP: True\n"
    )
    if mode == "hard":
        informal += "\nANSWER:\nThe answer is 1.\n"
    _write(candidate_dir / "informal_step.md", informal)
    attempt_dir = candidate_dir / "lean4_attempt_001"
    _write(
        attempt_dir / "compile.json",
        json.dumps({"attempt_status": "final_success"}),
    )

    state = {
        "problem_id": problem_id,
        "problem_mode": mode,
        "status": "failed",
        "proof_steps": [
            {
                "proof_step_index": 1,
                "status": "final_success",
                "informal_candidates": [
                    {
                        "informal_candidate_index": 1,
                        "status": "abandoned",
                        "lean_translation_attempts": [
                            {"lean_translation_attempt_index": 1, "status": "lemma_failed"}
                        ],
                    },
                    {
                        "informal_candidate_index": 2,
                        "status": "accepted",
                        "lean_translation_attempts": [
                            {"lean_translation_attempt_index": 1, "status": "final_success"}
                        ],
                    },
                ],
            }
        ],
    }
    _write(run / "state.json", json.dumps(state))
    solution = render_source(
        prelude,
        context,
        [declarations],
        placeholder=filled_placeholder,
        theorem_body=body,
    ).text
    if mismatched_final:
        solution += "-- mismatch\n"
    _write(run / "final" / "solution.lean", solution)
    (run / "failure").mkdir(parents=True, exist_ok=True)
    if failed:
        _write(run / "failure" / "failure_report.md", "failed")
    return run


class AcceptedDatasetTests(unittest.TestCase):
    def test_groups_by_problem_and_applies_filesystem_filters(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            runs.mkdir()
            _make_run(runs, "run_b")
            _make_run(runs, "run_a", mode="hard")
            _make_run(runs, "failed_run", failed=True)
            (runs / "missing_state").mkdir()
            _make_run(runs / "archive", "nested_run")

            dataset, counts, warnings = build_dataset(runs)

            self.assertEqual(
                list(dataset["problems"]),
                ["shared_problem", "shared_problem_run2"],
            )
            first = dataset["problems"]["shared_problem"]["datapoints"]
            second = dataset["problems"]["shared_problem_run2"]["datapoints"]
            self.assertEqual(first[0]["metadata"]["run_id"], "run_a")
            self.assertEqual(second[0]["metadata"]["run_id"], "run_b")
            self.assertEqual(counts["accepted_runs"], 2)
            self.assertEqual(counts["datapoints"], 2)
            self.assertEqual(counts["ignored_nonempty_failure"], 1)
            self.assertEqual(warnings, [])
            self.assertIn("NEXT STEP:\nProve the helper", first[0]["input"]["next_step"])
            self.assertIn("ANSWER:\nThe answer is 1.", first[0]["input"]["next_step"])
            self.assertNotIn("INTERMEDIATE REASONING", first[0]["input"]["next_step"])
            self.assertIn("def answer : Nat := by\n  sorry", first[0]["input"]["current_lean4_state"])
            self.assertIn("def answer : Nat := by\n  exact 1", first[0]["output"]["next_lean4_state"])

    def test_skips_a_generated_name_that_conflicts_with_a_real_problem(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            runs.mkdir()
            _make_run(runs, "a_original", problem_id="problem")
            _make_run(runs, "b_additional", problem_id="problem")
            _make_run(runs, "c_real_suffix", problem_id="problem_run2")

            dataset, counts, warnings = build_dataset(runs)

            self.assertEqual(list(dataset["problems"]), ["problem", "problem_run2"])
            self.assertEqual(counts["ignored_name_conflict"], 1)
            self.assertEqual(len(warnings), 1)
            self.assertIn("b_additional", warnings[0])
            self.assertIn("problem_run2", warnings[0])

    def test_rejects_a_reconstructed_final_state_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            runs.mkdir()
            _make_run(runs, "bad", mismatched_final=True)
            with self.assertRaisesRegex(DatasetBuildError, "does not match final/solution.lean"):
                build_dataset(runs)


if __name__ == "__main__":
    unittest.main()
