"""
ProblemLoader (§3/§21), Config load (§2), InputValidator (§4/§8).
"""
from __future__ import annotations
import json, os
from dataclasses import dataclass
from typing import List

from .models import Config
from .structure import extract_header
from .verifier_client import VerifierClient, build_compile_json
from .structure import StructureResult


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
    return Problem(pid, problem_dir, cfg,
                   open(ip).read(), open(cp).read(), open(bp).read(), cp, bp, ip)


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""              # "" | context_failed | initial_body_failed
    header: str = ""
    context_check: object = None
    initial_body_check: object = None


class InputValidator:
    def __init__(self, verifier: VerifierClient = None):
        self.verifier = verifier or VerifierClient()

    def validate(self, problem: Problem) -> ValidationResult:
        prelude = problem.config.lean_prelude
        # header extraction (fails loudly if no ':= by')
        header = extract_header(problem.body_initial)

        # context check: prelude + context must compile
        ctx_cp = self.verifier.check(prelude, problem.context, [], "", "")
        if not ctx_cp.declaration_check.passed:
            return ValidationResult(False, "context_failed", header, ctx_cp, None)

        # initial body check: + body_initial, sorry allowed
        body_cp = self.verifier.check(prelude, problem.context, [], "", problem.body_initial)
        if not (body_cp.declaration_check.passed and body_cp.body_check.passed):
            return ValidationResult(False, "initial_body_failed", header, ctx_cp, body_cp)

        return ValidationResult(True, "", header, ctx_cp, body_cp)
