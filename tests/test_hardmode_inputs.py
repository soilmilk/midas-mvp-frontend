#!/usr/bin/env python3
"""Phase 1 Hard Mode input, placeholder, and initial-validation gate."""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from midas.artifacts import LeanArtifactLogger, Paths
from midas.models import Config
from midas.problem import (InputValidator, Problem, ProblemInputError,
                           load_problem)
from midas.structure import (PlaceholderError, check_filled_placeholder,
                             extract_placeholder_info)

FIX = ROOT / "tests" / "fixtures" / "hard_mode"
CONTEXT = (FIX / "context.lean").read_text()
PLACEHOLDER = (FIX / "placeholder.lean").read_text()
BODY = (FIX / "body_initial.lean").read_text()
FILLED = (FIX / "valid_filled_placeholder.lean").read_text()

rows = []


def check(label, ok, detail=""):
    rows.append((label, bool(ok), detail))


def make_problem(root: Path, mode=None, placeholder=None):
    inp = root / "input"
    inp.mkdir(parents=True)
    config = {}
    if mode is not None:
        config["problem_mode"] = mode
    (root / "config.json").write_text(json.dumps(config))
    (inp / "informal_problem.md").write_text("Find the correct answer.")
    (inp / "context.lean").write_text(CONTEXT)
    (inp / "body_initial.lean").write_text(BODY)
    if placeholder is not None:
        (inp / "placeholder.lean").write_text(placeholder)


tmp = Path(tempfile.mkdtemp(prefix="midas-hard-inputs-"))
try:
    default_easy = tmp / "default_easy"
    make_problem(default_easy)
    loaded = load_problem(str(default_easy))
    check("omitted mode defaults to Easy", loaded.config.problem_mode == "easy")
    check("Easy placeholder fields use None",
          loaded.placeholder is None and loaded.placeholder_path is None)

    explicit_easy = tmp / "explicit_easy"
    make_problem(explicit_easy, "easy")
    easy_problem = load_problem(str(explicit_easy))
    check("explicit Easy without placeholder loads", easy_problem.placeholder is None)
    easy_result = InputValidator().validate(easy_problem)
    check("Easy initial validation remains two-pass",
          easy_result.ok and easy_result.compile_count == 2,
          easy_result.reason)

    hard = tmp / "hard"
    make_problem(hard, "hard", PLACEHOLDER)
    hard_problem = load_problem(str(hard))
    check("Hard placeholder source retained exactly",
          hard_problem.placeholder == PLACEHOLDER)
    check("Hard placeholder path retained",
          hard_problem.placeholder_path == str(hard / "input" / "placeholder.lean"))

    missing = tmp / "missing"
    make_problem(missing, "hard")
    try:
        load_problem(str(missing))
        check("missing Hard placeholder fails cleanly", False, "did not raise")
    except ProblemInputError as error:
        check("missing Hard placeholder fails cleanly",
              error.code == "missing_placeholder", error.code)

    unexpected = tmp / "unexpected"
    make_problem(unexpected, "easy", PLACEHOLDER)
    try:
        load_problem(str(unexpected))
        check("Easy placeholder fails cleanly", False, "did not raise")
    except ProblemInputError as error:
        check("Easy placeholder fails cleanly",
              error.code == "unexpected_placeholder_in_easy_mode", error.code)

    try:
        Config(problem_mode="mystery")
        check("unknown problem mode rejected", False, "did not raise")
    except ValidationError:
        check("unknown problem mode rejected", True)

    info = extract_placeholder_info(PLACEHOLDER)
    check("placeholder header extracted exactly",
          info.header == "def answer : Nat := by", repr(info.header))
    check("placeholder name extracted", info.name == "answer", info.name)
    check("placeholder source retained exactly", info.source == PLACEHOLDER)

    malformed = {
        "missing tactic marker": "def answer : Nat := 5\n",
        "no sorry": FILLED,
        "two definitions": PLACEHOLDER + "\ndef other : Nat := by\n  sorry\n",
        "lemma plus definition": "lemma helper : True := by trivial\n" + PLACEHOLDER,
        "unsupported abbrev": "abbrev answer : Nat := by\n  sorry\n",
        "unsupported theorem": "theorem answer : Nat := by\n  sorry\n",
        "unsupported instance": "instance answer : Inhabited Nat := by\n  sorry\n",
        "unrelated example": PLACEHOLDER + "\nexample : True := by trivial\n",
        "import injection": "import Mathlib\n" + PLACEHOLDER,
        "namespace injection": "namespace Hidden\n" + PLACEHOLDER + "end Hidden\n",
    }
    for label, source in malformed.items():
        try:
            extract_placeholder_info(source)
            check(f"reject {label}", False, "accepted")
        except PlaceholderError as error:
            check(f"reject {label}", True, str(error))

    commented = "\n-- answer fixture\n/- nested /- comment -/ ok -/\n" + PLACEHOLDER
    commented_info = extract_placeholder_info(commented)
    check("comments and leading whitespace are supported",
          commented_info.header == info.header and commented_info.name == info.name)

    check("valid filled placeholder accepted",
          check_filled_placeholder(FILLED, info.header, info.name).ok)
    check("changed filled name rejected",
          not check_filled_placeholder(
              FILLED.replace("answer", "other", 1), info.header, info.name).ok)
    check("changed filled header rejected",
          not check_filled_placeholder(
              FILLED.replace("Nat", "Int", 1), info.header, info.name).ok)
    check("filled placeholder with sorry rejected",
          not check_filled_placeholder(PLACEHOLDER, info.header, info.name).ok)
    check("filled placeholder with auxiliary declaration rejected",
          not check_filled_placeholder(FILLED + "\ndef other : Nat := 0\n",
                                       info.header, info.name).ok)

    validator = InputValidator()
    valid_result = validator.validate(hard_problem)
    check("valid Hard input validates", valid_result.ok, valid_result.reason)
    check("validation returns both exact headers",
          valid_result.placeholder_header == info.header and
          valid_result.theorem_header == "theorem main : IsCorrectAnswer answer := by")
    check("successful initial validation counts two compiles",
          valid_result.compile_count == 2, str(valid_result.compile_count))

    ill_placeholder = Problem(
        **{**hard_problem.__dict__,
           "placeholder": "def answer : Nat := by\n  exact True\n  sorry\n"})
    ill_placeholder_result = validator.validate(ill_placeholder)
    check("ill-typed initial placeholder is classified",
          not ill_placeholder_result.ok and
          ill_placeholder_result.reason == "initial_placeholder_or_body_failed",
          ill_placeholder_result.reason)

    ill_body = Problem(
        **{**hard_problem.__dict__,
           "body_initial": "theorem main : IsCorrectAnswer answer := by\n  exact missing\n"})
    ill_body_result = validator.validate(ill_body)
    check("invalid initial theorem is classified",
          not ill_body_result.ok and
          ill_body_result.reason == "initial_placeholder_or_body_failed",
          ill_body_result.reason)

    bad_context = Problem(
        **{**hard_problem.__dict__,
           "context": "def broken : MissingType := by sorry\n"})
    bad_context_result = validator.validate(bad_context)
    check("failed prefix is context_failed",
          not bad_context_result.ok and bad_context_result.reason == "context_failed",
          bad_context_result.reason)
    check("failed prefix counts one compile",
          bad_context_result.compile_count == 1,
          str(bad_context_result.compile_count))

    artifact_root = tmp / "runs"
    logger = LeanArtifactLogger(Paths(str(artifact_root), "hard"))
    logger.write_inputs("{}", "problem", CONTEXT, BODY, placeholder=PLACEHOLDER)
    copied = artifact_root / "hard" / "input" / "placeholder.lean"
    check("Hard input artifact preserves placeholder",
          copied.read_text() == PLACEHOLDER)
    easy_logger = LeanArtifactLogger(Paths(str(artifact_root), "easy"))
    easy_logger.write_inputs("{}", "problem", CONTEXT, BODY)
    check("Easy input artifacts omit placeholder",
          not (artifact_root / "easy" / "input" / "placeholder.lean").exists())
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print(f"{'check':<55}result  detail")
print("-" * 92)
all_ok = True
for label, ok, detail in rows:
    all_ok &= ok
    print(f"{label:<55}{'PASS' if ok else 'FAIL':<8}{detail}")
print("-" * 92)
print(f"\nHARD MODE INPUTS: {'PASS' if all_ok else 'FAIL'}")
sys.exit(0 if all_ok else 1)
