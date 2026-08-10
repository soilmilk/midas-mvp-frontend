"""
ProblemLoader (§3/§21), Config load (§2), InputValidator (§4/§8).
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass
from typing import Optional

from .models import Config
from .structure import (PlaceholderError, check_structure, extract_header,
                        extract_placeholder_info)
from .verifier_client import VerifierClient


@dataclass
class Problem:
    problem_id: str
    root: str
    config: Config
    informal_problem: str
    context: str
    body_initial: str
    context_path: str
    initial_body_path: str
    informal_problem_path: str
    placeholder: Optional[str] = None
    placeholder_path: Optional[str] = None


class ProblemInputError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def load_config(path: str) -> Config:
    with open(path) as f:
        return Config(**json.load(f))


def load_problem(problem_dir: str) -> Problem:
    problem_dir = os.path.abspath(problem_dir)
    pid = os.path.basename(problem_dir.rstrip("/"))
    inp = os.path.join(problem_dir, "input")
    cfg = load_config(os.path.join(problem_dir, "config.json"))
    ip = os.path.join(inp, "informal_problem.md")
    cp = os.path.join(inp, "context.lean")
    bp = os.path.join(inp, "body_initial.lean")
    pp = os.path.join(inp, "placeholder.lean")
    placeholder_exists = os.path.isfile(pp)
    if cfg.problem_mode == "hard" and not placeholder_exists:
        raise ProblemInputError(
            "missing_placeholder",
            f"Hard Mode problem {pid!r} requires input/placeholder.lean")
    if cfg.problem_mode == "easy" and placeholder_exists:
        raise ProblemInputError(
            "unexpected_placeholder_in_easy_mode",
            f"Easy Mode problem {pid!r} must not contain input/placeholder.lean")
    placeholder = open(pp).read() if placeholder_exists else None
    return Problem(pid, problem_dir, cfg,
                   open(ip).read(), open(cp).read(), open(bp).read(), cp, bp, ip,
                   placeholder, pp if placeholder_exists else None)


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""
    placeholder_header: str = ""
    placeholder_name: str = ""
    placeholder_initial_source: str = ""
    theorem_header: str = ""
    compile_count: int = 0
    context_check: object = None
    initial_body_check: object = None

    @property
    def header(self) -> str:
        """Compatibility alias for existing Easy Mode loop callers."""
        return self.theorem_header


class InputValidator:
    def __init__(self, verifier: VerifierClient = None):
        self.verifier = verifier or VerifierClient()

    def validate(self, problem: Problem) -> ValidationResult:
        prelude = problem.config.lean_prelude
        mode = problem.config.problem_mode
        if mode == "hard" and problem.placeholder is None:
            return ValidationResult(False, "missing_placeholder")
        if mode == "easy" and problem.placeholder is not None:
            return ValidationResult(False, "unexpected_placeholder_in_easy_mode")

        placeholder_header = ""
        placeholder_name = ""
        placeholder_source = ""
        if mode == "hard":
            try:
                placeholder_info = extract_placeholder_info(problem.placeholder or "")
            except PlaceholderError:
                return ValidationResult(False, "malformed_placeholder")
            placeholder_header = placeholder_info.header
            placeholder_name = placeholder_info.name
            placeholder_source = placeholder_info.source

        try:
            theorem_header = extract_header(problem.body_initial)
        except Exception:
            reason = ("initial_placeholder_or_body_failed"
                      if mode == "hard" else "initial_body_failed")
            return ValidationResult(
                False, reason, placeholder_header, placeholder_name,
                placeholder_source)

        theorem_structure = check_structure("", problem.body_initial, theorem_header, [])
        if not theorem_structure.ok:
            reason = ("initial_placeholder_or_body_failed"
                      if mode == "hard" else "initial_body_failed")
            return ValidationResult(
                False, reason, placeholder_header, placeholder_name,
                placeholder_source, theorem_header)

        checkpoint = self.verifier.check(
            prelude,
            problem.context,
            [],
            "",
            problem.body_initial,
            placeholder=placeholder_source,
        )
        compile_count = 1 if not checkpoint.declaration_check.passed else 2
        base = dict(
            placeholder_header=placeholder_header,
            placeholder_name=placeholder_name,
            placeholder_initial_source=placeholder_source,
            theorem_header=theorem_header,
            compile_count=compile_count,
            context_check=checkpoint,
            initial_body_check=checkpoint,
        )
        if not checkpoint.declaration_check.passed:
            return ValidationResult(False, "context_failed", **base)
        if not checkpoint.body_check.passed:
            reason = ("initial_placeholder_or_body_failed"
                      if mode == "hard" else "initial_body_failed")
            return ValidationResult(False, reason, **base)
        return ValidationResult(True, **base)
