#!/usr/bin/env python3
"""Build accepted Lean-state transition datapoints from first-level Midas runs."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from midas.reconstructor import render_source


REPO_ROOT = Path(__file__).resolve().parent
RUNS_DIR = REPO_ROOT / "runs"
OUTPUT_PATH = REPO_ROOT / "datasets" / "accepted_steps.json"

ACCEPTED_STEP_STATUSES = {"accepted", "final_success"}
ACCEPTED_ATTEMPT_STATUSES = {"accepted", "final_success"}
_INFORMAL_HEADING = re.compile(
    r"(?m)^[ \t]*(INTERMEDIATE REASONING|NEXT STEP|PROOF|STEP USEFULNESS|"
    r"IS_FINAL_STEP|ANSWER|IDEAS FOR THE FUTURE):[ \t]*(.*)$"
)


class DatasetBuildError(RuntimeError):
    """An accepted run is internally inconsistent or missing required data."""


def _read_text(path: Path, label: str) -> str:
    if not path.is_file():
        raise DatasetBuildError(f"missing {label}: {path}")
    return path.read_text(encoding="utf-8")


def _read_json(path: Path, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(_read_text(path, label))
    except json.JSONDecodeError as error:
        raise DatasetBuildError(f"invalid JSON in {label} {path}: {error}") from error
    if not isinstance(value, dict):
        raise DatasetBuildError(f"{label} must contain a JSON object: {path}")
    return value


def _normalized_final_newline(text: str) -> str:
    return text.rstrip("\n") + "\n"


def _extract_informal_step(raw: str, *, include_answer: bool, label: str) -> str:
    matches = list(_INFORMAL_HEADING.finditer(raw))
    values: Dict[str, str] = {}
    for index, match in enumerate(matches):
        name = match.group(1)
        if name in values:
            raise DatasetBuildError(f"duplicate {name} section in {label}")
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw)
        values[name] = (match.group(2) + raw[match.end():end]).strip()

    required = ["NEXT STEP", "PROOF"]
    if include_answer:
        required.append("ANSWER")
    missing = [name for name in required if not values.get(name)]
    if missing:
        raise DatasetBuildError(
            f"missing or empty {', '.join(missing)} section in {label}"
        )

    parts = [
        f"NEXT STEP:\n{values['NEXT STEP']}",
        f"PROOF:\n{values['PROOF']}",
    ]
    if include_answer:
        parts.append(f"ANSWER:\n{values['ANSWER']}")
    return "\n\n".join(parts)


def _accepted_selection(step: Dict[str, Any], run_id: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    step_index = step.get("proof_step_index")
    candidates = [
        candidate
        for candidate in step.get("informal_candidates", [])
        if candidate.get("status") == "accepted"
    ]
    if len(candidates) != 1:
        raise DatasetBuildError(
            f"{run_id} proof_step_{step_index:03d}: expected exactly one accepted "
            f"candidate, found {len(candidates)}"
        )
    candidate = candidates[0]
    attempts = [
        attempt
        for attempt in candidate.get("lean_translation_attempts", [])
        if attempt.get("status") in ACCEPTED_ATTEMPT_STATUSES
    ]
    if len(attempts) != 1:
        raise DatasetBuildError(
            f"{run_id} proof_step_{step_index:03d}: expected exactly one accepted "
            f"Lean attempt, found {len(attempts)}"
        )
    return candidate, attempts[0]


def _validate_compile_status(
    run_dir: Path,
    step_index: int,
    candidate_index: int,
    attempt_index: int,
    expected_status: str,
) -> None:
    compile_path = (
        run_dir
        / "artifacts"
        / "proof_steps"
        / f"proof_step_{step_index:03d}"
        / f"informal_candidate_{candidate_index:03d}"
        / f"lean4_attempt_{attempt_index:03d}"
        / "compile.json"
    )
    compile_data = _read_json(compile_path, "accepted compile result")
    actual_status = compile_data.get("attempt_status")
    if actual_status != expected_status:
        raise DatasetBuildError(
            f"{run_dir.name} proof_step_{step_index:03d}: state attempt status "
            f"{expected_status!r} does not match compile status {actual_status!r}"
        )


def _build_run_datapoints(run_dir: Path) -> Tuple[str, List[Dict[str, Any]]]:
    run_id = run_dir.name
    state = _read_json(run_dir / "state.json", "run state")
    config = _read_json(run_dir / "config.json", "run config")
    problem_id = state.get("problem_id")
    if not isinstance(problem_id, str) or not problem_id:
        raise DatasetBuildError(f"{run_id}: state.json has no non-empty problem_id")

    problem_mode = state.get("problem_mode") or config.get("problem_mode") or "easy"
    if problem_mode not in {"easy", "hard"}:
        raise DatasetBuildError(f"{run_id}: unsupported problem_mode {problem_mode!r}")

    prelude = config.get("lean_prelude", [])
    if not isinstance(prelude, list) or not all(isinstance(line, str) for line in prelude):
        raise DatasetBuildError(f"{run_id}: config lean_prelude must be a list of strings")
    context = _read_text(run_dir / "input" / "context.lean", "Lean context")
    previous_body = _read_text(
        run_dir / "input" / "body_initial.lean", "initial theorem body"
    )
    initial_placeholder = ""
    if problem_mode == "hard":
        initial_placeholder = _read_text(
            run_dir / "input" / "placeholder.lean", "initial placeholder"
        )

    accepted_steps = [
        step
        for step in state.get("proof_steps", [])
        if step.get("status") in ACCEPTED_STEP_STATUSES
    ]
    try:
        accepted_steps.sort(key=lambda step: int(step["proof_step_index"]))
    except (KeyError, TypeError, ValueError) as error:
        raise DatasetBuildError(f"{run_id}: accepted step has an invalid index") from error
    if not accepted_steps:
        raise DatasetBuildError(f"{run_id}: accepted run contains no accepted proof steps")

    accepted_declarations: List[str] = []
    current_state = render_source(
        prelude,
        context,
        accepted_declarations,
        placeholder=initial_placeholder,
        theorem_body=previous_body,
    ).text
    datapoints: List[Dict[str, Any]] = []

    for step in accepted_steps:
        step_index = int(step["proof_step_index"])
        candidate, attempt = _accepted_selection(step, run_id)
        candidate_index = int(candidate["informal_candidate_index"])
        attempt_index = int(attempt["lean_translation_attempt_index"])
        attempt_status = attempt["status"]
        is_final = attempt_status == "final_success"

        _validate_compile_status(
            run_dir,
            step_index,
            candidate_index,
            attempt_index,
            attempt_status,
        )

        informal_path = (
            run_dir
            / "artifacts"
            / "proof_steps"
            / f"proof_step_{step_index:03d}"
            / f"informal_candidate_{candidate_index:03d}"
            / "informal_step.md"
        )
        informal_step = _extract_informal_step(
            _read_text(informal_path, "accepted informal step"),
            include_answer=(problem_mode == "hard" and is_final),
            label=str(informal_path),
        )

        accepted_dir = run_dir / "accepted" / f"proof_step_{step_index:03d}"
        declarations = _read_text(
            accepted_dir / "declarations.lean", "accepted declarations"
        )
        body = _read_text(accepted_dir / "body.lean", "accepted theorem body")
        accepted_declarations.append(declarations)

        placeholder = initial_placeholder
        if problem_mode == "hard" and is_final:
            placeholder = _read_text(
                accepted_dir / "placeholder.lean", "accepted filled placeholder"
            )

        next_state = render_source(
            prelude,
            context,
            accepted_declarations,
            placeholder=placeholder,
            theorem_body=body,
        ).text

        if is_final:
            final_solution = _read_text(
                run_dir / "final" / "solution.lean", "final Lean solution"
            )
            if _normalized_final_newline(next_state) != _normalized_final_newline(final_solution):
                raise DatasetBuildError(
                    f"{run_id} proof_step_{step_index:03d}: reconstructed final state "
                    "does not match final/solution.lean"
                )
            next_state = final_solution

        datapoints.append(
            {
                "input": {
                    "current_lean4_state": current_state,
                    "next_step": informal_step,
                },
                "output": {"next_lean4_state": next_state},
                "metadata": {
                    "run_id": run_id,
                    "proof_step_index": step_index,
                    "informal_candidate_index": candidate_index,
                    "lean_attempt_index": attempt_index,
                    "problem_mode": problem_mode,
                    "is_final_step": is_final,
                },
            }
        )
        current_state = next_state
        previous_body = body

    if not datapoints[-1]["metadata"]["is_final_step"]:
        raise DatasetBuildError(f"{run_id}: accepted run has no final-success proof step")
    return problem_id, datapoints


def build_dataset(
    runs_dir: Path = RUNS_DIR,
) -> Tuple[Dict[str, Any], Counter, List[str]]:
    if not runs_dir.is_dir():
        raise DatasetBuildError(f"runs directory does not exist: {runs_dir}")

    accepted_runs: List[Tuple[Path, str]] = []
    counts: Counter = Counter()
    warnings: List[str] = []
    run_dirs = sorted((path for path in runs_dir.iterdir() if path.is_dir()), key=lambda p: p.name)
    counts["directories_scanned"] = len(run_dirs)

    for run_dir in run_dirs:
        if not (run_dir / "state.json").is_file():
            counts["ignored_missing_state"] += 1
            continue
        failure_dir = run_dir / "failure"
        if failure_dir.is_dir() and any(failure_dir.iterdir()):
            counts["ignored_nonempty_failure"] += 1
            continue
        if not (run_dir / "final" / "solution.lean").is_file():
            counts["ignored_missing_solution"] += 1
            continue

        state = _read_json(run_dir / "state.json", "run state")
        problem_id = state.get("problem_id")
        if not isinstance(problem_id, str) or not problem_id:
            raise DatasetBuildError(
                f"{run_dir.name}: state.json has no non-empty problem_id"
            )
        accepted_runs.append((run_dir, problem_id))

    # Reserve every real problem ID before creating suffixed names. This makes
    # collision handling independent of run-directory ordering.
    reserved_problem_ids = {problem_id for _, problem_id in accepted_runs}
    occurrences: Counter = Counter()
    problems: Dict[str, Dict[str, Any]] = {}
    for run_dir, problem_id in accepted_runs:
        occurrences[problem_id] += 1
        run_number = occurrences[problem_id]
        dataset_problem_id = (
            problem_id if run_number == 1 else f"{problem_id}_run{run_number}"
        )
        conflicts_with_real_problem = (
            run_number > 1 and dataset_problem_id in reserved_problem_ids
        )
        if conflicts_with_real_problem or dataset_problem_id in problems:
            warning = (
                f"run {run_dir.name!r} was not included: generated problem name "
                f"{dataset_problem_id!r} conflicts with another problem"
            )
            warnings.append(warning)
            counts["ignored_name_conflict"] += 1
            continue

        actual_problem_id, datapoints = _build_run_datapoints(run_dir)
        if actual_problem_id != problem_id:
            raise DatasetBuildError(
                f"{run_dir.name}: problem_id changed while building the dataset"
            )
        problems[dataset_problem_id] = {"datapoints": datapoints}
        counts["accepted_runs"] += 1
        counts["datapoints"] += len(datapoints)

    problems = {problem_id: problems[problem_id] for problem_id in sorted(problems)}
    counts["problems"] = len(problems)
    return {"schema_version": 1, "problems": problems}, counts, warnings


def write_dataset(dataset: Dict[str, Any], output_path: Path = OUTPUT_PATH) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=output_path.parent,
            prefix=f".{output_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            json.dump(dataset, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, output_path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def main() -> None:
    try:
        dataset, counts, warnings = build_dataset()
        write_dataset(dataset)
    except DatasetBuildError as error:
        raise SystemExit(f"dataset build failed: {error}") from error

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    print(f"Wrote {OUTPUT_PATH}")
    print(f"First-level directories scanned: {counts['directories_scanned']}")
    print(f"Accepted runs: {counts['accepted_runs']}")
    print(f"Problems: {counts['problems']}")
    print(f"Datapoints: {counts['datapoints']}")
    print(f"Ignored without state.json: {counts['ignored_missing_state']}")
    print(f"Ignored with non-empty failure/: {counts['ignored_nonempty_failure']}")
    print(f"Ignored without final/solution.lean: {counts['ignored_missing_solution']}")
    print(f"Ignored due to generated-name conflicts: {counts['ignored_name_conflict']}")


if __name__ == "__main__":
    main()
