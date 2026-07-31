"""
Artifact layout (§6) + LeanArtifactLogger + StateManager persistence.

Exact §6 tree:
  runs/<pid>/ config.json state.json
    input/ (informal_problem.md context.lean [placeholder.lean] body_initial.lean *_check.json)
    artifacts/proof_steps/proof_step_NNN/informal_candidate_NNN/{reasoning_prompt.md,informal_step.md}/lean4_attempt_NNN/{prompt,output,parsed Lean,check inputs,compile.json}
    accepted/proof_step_NNN/{declarations.lean,[placeholder.lean],body.lean}
    tmp/ final/{solution.lean,solution.md,[placeholder.lean],body.lean}
    failure/{failure_report.md,...}
"""
from __future__ import annotations
import json, os, shutil
from typing import Optional

from .models import ProofRunState, CompileJson


def _w(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


class Paths:
    def __init__(self, runs_root: str, pid: str):
        self.root = os.path.join(runs_root, pid)
        self.input = os.path.join(self.root, "input")
        self.artifacts = os.path.join(self.root, "artifacts", "proof_steps")
        self.accepted = os.path.join(self.root, "accepted")
        self.tmp = os.path.join(self.root, "tmp")
        self.final = os.path.join(self.root, "final")
        self.failure = os.path.join(self.root, "failure")

    def ps(self, i):  return os.path.join(self.artifacts, f"proof_step_{i:03d}")
    def ic(self, i, j):  return os.path.join(self.ps(i), f"informal_candidate_{j:03d}")
    def la(self, i, j, k):  return os.path.join(self.ic(i, j), f"lean4_attempt_{k:03d}")
    def accepted_ps(self, i):  return os.path.join(self.accepted, f"proof_step_{i:03d}")


class LeanArtifactLogger:
    def __init__(self, paths: Paths):
        self.p = paths
        for d in (self.p.input, self.p.artifacts, self.p.accepted,
                  self.p.tmp, self.p.final, self.p.failure):
            os.makedirs(d, exist_ok=True)

    # -- input --
    def write_inputs(self, config_json: str, informal: str, context: str, body_initial: str,
                     placeholder: Optional[str] = None):
        _w(os.path.join(self.p.root, "config.json"), config_json)
        _w(os.path.join(self.p.input, "informal_problem.md"), informal)
        _w(os.path.join(self.p.input, "context.lean"), context)
        if placeholder is not None:
            _w(os.path.join(self.p.input, "placeholder.lean"), placeholder)
        _w(os.path.join(self.p.input, "body_initial.lean"), body_initial)

    def write_input_check(self, name: str, cj: CompileJson):
        _w(os.path.join(self.p.input, name), cj.model_dump_json(indent=2))

    # -- reasoning (ic level) --
    def write_reasoning_prompt(self, i, j, prompt: str):
        path = os.path.join(self.p.ic(i, j), "reasoning_prompt.md")
        _w(path, prompt)
        return path

    def write_reasoning_output(self, i, j, informal_step: str):
        path = os.path.join(self.p.ic(i, j), "informal_step.md")
        _w(path, informal_step)
        return path

    def write_reasoning_error(self, i, j, error: str):
        path = os.path.join(self.p.ic(i, j), "reasoning_call_error.txt")
        _w(path, error)
        return path

    def write_reasoning(self, i, j, prompt: str, informal_step: str):
        self.write_reasoning_prompt(i, j, prompt)
        self.write_reasoning_output(i, j, informal_step)

    # -- translation (la level) --
    def write_translation_prompt(self, i, j, k, prompt: str):
        path = os.path.join(self.p.la(i, j, k), "translator_prompt.md")
        _w(path, prompt)
        return path

    def write_translation_output(self, i, j, k, raw: str):
        path = os.path.join(self.p.la(i, j, k), "raw_translator_output.md")
        _w(path, raw)
        return path

    def write_parsed_translation(self, i, j, k,
                                 declarations: Optional[str], body: Optional[str],
                                 placeholder: Optional[str] = None):
        d = self.p.la(i, j, k)
        dp = pp = bp = None
        if declarations is not None:
            dp = os.path.join(d, "declarations.lean")
            _w(dp, declarations)
        if placeholder is not None:
            pp = os.path.join(d, "placeholder.lean")
            _w(pp, placeholder)
        if body is not None:
            bp = os.path.join(d, "body.lean")
            _w(bp, body)
        return dp, pp, bp

    def write_attempt_source(self, i, j, k, name: str, source: str):
        path = os.path.join(self.p.la(i, j, k), name)
        _w(path, source)
        return path

    def write_compile(self, i, j, k, cj: CompileJson):
        path = os.path.join(self.p.la(i, j, k), "compile.json")
        _w(path, cj.model_dump_json(indent=2))
        return path

    def write_translation(self, i, j, k, prompt: str, raw: str,
                          declarations: Optional[str], body: Optional[str], cj: CompileJson,
                          placeholder: Optional[str] = None):
        self.write_translation_prompt(i, j, k, prompt)
        self.write_translation_output(i, j, k, raw)
        dp, pp, bp = self.write_parsed_translation(
            i, j, k, declarations, body, placeholder
        )
        return dp, pp, bp, self.write_compile(i, j, k, cj)

    def copy_accepted(self, i, declarations: str, body: str,
                      placeholder: Optional[str] = None):
        d = self.p.accepted_ps(i)
        _w(os.path.join(d, "declarations.lean"), declarations)
        if placeholder is not None:
            _w(os.path.join(d, "placeholder.lean"), placeholder)
        _w(os.path.join(d, "body.lean"), body)

    # -- final / failure --
    def write_final_candidate(self, solution: str):
        path = os.path.join(self.p.tmp, "final_candidate.lean")
        _w(path, solution)
        return path

    def write_final(self, solution: str, solution_md: str,
                    placeholder: Optional[str] = None,
                    body: Optional[str] = None):
        _w(os.path.join(self.p.final, "solution.lean"), solution)
        _w(os.path.join(self.p.final, "solution.md"), solution_md)
        if placeholder is not None:
            _w(os.path.join(self.p.final, "placeholder.lean"), placeholder)
        if body is not None:
            _w(os.path.join(self.p.final, "body.lean"), body)
        return os.path.join(self.p.final, "solution.lean")

    def write_failure(self, report: str, last_verified: str, last_body: str):
        _w(os.path.join(self.p.failure, "failure_report.md"), report)
        _w(os.path.join(self.p.failure, "last_verified.lean"), last_verified)
        _w(os.path.join(self.p.failure, "last_body.lean"), last_body)


class StateManager:
    def __init__(self, paths: Paths):
        self.p = paths

    def save(self, state: ProofRunState):
        _w(os.path.join(self.p.root, "state.json"), state.model_dump_json(indent=2))

    @staticmethod
    def load(root: str) -> ProofRunState:
        with open(os.path.join(root, "state.json")) as f:
            return ProofRunState.model_validate_json(f.read())
