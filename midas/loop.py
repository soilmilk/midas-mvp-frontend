"""
The core MVP loop — SPEC.md §18, wiring §21's modules. LimitController (§2/§16) and
FailureController (§16) live here. Entry point: run_problem().
"""
from __future__ import annotations
import os, re, time
from typing import List, Optional

from .models import (ProofRunState, ProofStep, InformalCandidate, LeanTranslationAttempt,
                     RunStats, CompileJson, CheckReport, Diagnostic)
from .problem import load_problem, InputValidator, Problem
from .agents import ReasoningAgent, TranslationAgent, TranslationRepairContext
from .parser import parse_translator_output
from .structure import (extract_header, check_structure, body_contains_sorry, declared_names)
from .verifier_client import VerifierClient, make_verifier, build_compile_json
from .reconstructor import reconstruct, render_source
from .artifacts import Paths, LeanArtifactLogger, StateManager

CONSID = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "considerations")


def _read(p): return open(p).read()
def _now(): return time.time()


def _extract_informal(text: str) -> Optional[str]:
    """Return the candidate beginning at NEXT STEP, or None when it is absent."""
    text = text.strip()
    if not text:
        return ""
    m = re.search(r"(?im)^\s*NEXT STEP:\s*", text)
    return text[m.start():] if m else None


def _extract_next_step(informal: str) -> str:
    """Return the complete proposition, excluding its proof, for reasoning retries."""
    m = re.search(r"(?is)NEXT STEP:\s*(.+?)(?:\n\s*PROOF:|\Z)", informal)
    return m.group(1).strip() if m else ""


def _feedback_from_errors(errs: List[Diagnostic]) -> str:
    return "\n".join(f"{e.file}:{e.line}:{e.col}: {e.severity}"
                     f"{'('+e.code+')' if e.code else ''}: {e.message}" for e in errs) or "(no detail)"


def _accepted_summary(decls: List[str]) -> str:
    if not decls:
        return "(none)"
    return "\n\n".join(decls)


def _submitted_source(prelude: List[str], context: str, accepted_decls: List[str],
                      declarations: str, body: str, warm: bool) -> str:
    """Reproduce the source layout used by the selected verifier for location mapping."""
    submitted_prelude = ([line for line in (prelude or [])
                          if not line.strip().startswith("import ")]
                         if warm else prelude)
    return render_source(
        submitted_prelude,
        context,
        accepted_decls,
        candidate_declarations=declarations,
        theorem_body=body,
    ).text


def _line_range(source: str, fragment: str, start_at: int = 0) -> tuple[int, int]:
    if not fragment.strip():
        return (0, -1)
    pos = source.find(fragment.rstrip(), start_at)
    if pos < 0:
        return (0, -1)
    first = source.count("\n", 0, pos) + 1
    return first, first + fragment.rstrip().count("\n")


def _render_repair_diagnostics(errors: List[Diagnostic], source: str, declarations: str,
                               body: str) -> str:
    """Render every diagnostic with its owning region and a numbered nearby excerpt."""
    decl_range = _line_range(source, declarations)
    body_range = _line_range(source, body, source.find(declarations.rstrip()) + len(declarations.rstrip())
                             if declarations.strip() else 0)
    lines = source.splitlines()
    rendered = []
    for index, error in enumerate(errors, 1):
        line, col = error.line, error.col
        if line <= 0:
            embedded = re.search(r"<req>:(\d+):(\d+):", error.message)
            if embedded:
                line, col = int(embedded.group(1)), int(embedded.group(2))
        if decl_range[0] <= line <= decl_range[1]:
            region = "rejected NEW DECLARATIONS"
        elif body_range[0] <= line <= body_range[1]:
            region = "rejected UPDATED THEOREM BODY"
        else:
            region = "prelude, context, or previously accepted code"
        code = f" ({error.code})" if error.code else ""
        item = [f"#### Error {index}",
                f"- Diagnostic: `{error.file}:{line}:{col}` {error.severity}{code}",
                f"- Source region: **{region}**",
                f"- Message: {error.message}"]
        if line > 0 and lines:
            lo, hi = max(1, line - 3), min(len(lines), line + 3)
            width = len(str(hi))
            excerpt = [f"{n:>{width}} | {lines[n - 1]}" for n in range(lo, hi + 1)]
            if line <= len(lines):
                excerpt.append(" " * width + " | " + " " * max(0, col) + "^")
            item.extend(["- Nearby submitted Lean code:", "```lean4", *excerpt, "```"])
        else:
            item.append("- Nearby submitted Lean code: unavailable because the verifier supplied no line number.")
        rendered.append("\n".join(item))
    return "\n\n".join(rendered) if rendered else "No structured diagnostics were returned."


class LimitController:
    def __init__(self, config, start: float):
        self.c, self.start = config, start

    def exceeded(self, state: ProofRunState) -> Optional[str]:
        if len(state.proof_steps) > self.c.max_proof_steps:
            return "max_proof_steps"
        if state.stats.total_lean_attempts >= self.c.max_total_lean_attempts:
            return "max_total_lean_attempts"
        if _now() - self.start >= self.c.max_runtime_seconds:
            return "max_runtime_seconds"
        return None


class FailureController:
    def __init__(self, logger: LeanArtifactLogger):
        self.logger = logger

    def write(self, state: ProofRunState, reason: str, last_verified: str, last_body: str,
              last_error: str, category_counts: dict):
        state.status = "failed"
        state.failure_reason = reason
        cats = ", ".join(f"{k}={v}" for k, v in sorted(category_counts.items())) or "(none)"
        report = f"""# Failure report — {state.problem_id}

- **Failure reason:** {reason}
- **Accepted proof steps:** {state.stats.accepted_proof_steps}
- **Informal candidates tried:** {sum(len(s.informal_candidates) for s in state.proof_steps)}
- **Lean attempts tried:** {state.stats.total_lean_attempts}
- **Lean compiles:** {state.stats.total_lean_compiles}
- **LLM calls:** {state.stats.total_llm_calls}
- **Failure categories:** {cats}

## Last compiler error
```
{last_error or '(none)'}
```

## Last verified Lean environment
```lean4
{last_verified}
```

## Latest theorem body artifact
```lean4
{last_body}
```

## Recommended next action
Inspect the most common failure category above and the last compiler error; if the informal
steps were sound but untranslatable, tighten FORMAL_TRANSLATION_CONSIDERATIONS.md; if the steps
themselves were wrong or too large, tighten INFORMAL_REASONING_CONSIDERATIONS.md.
"""
        self.logger.write_failure(report, last_verified, last_body)


def run_problem(problem_dir: str, runs_root: Optional[str] = None,
                reasoning_offline: Optional[List[str]] = None,
                translation_offline: Optional[List[str]] = None) -> ProofRunState:
    prob = load_problem(problem_dir)
    runs_root = runs_root or os.path.join(prob.root, "runs")
    paths = Paths(runs_root, prob.problem_id)
    logger = LeanArtifactLogger(paths)
    statemgr = StateManager(paths)
    verifier = make_verifier(prob.config)   # "fresh" (default) or "warm" (midas_proof_verifier)

    state = ProofRunState(
        problem_id=prob.problem_id,
        informal_problem_path=prob.informal_problem_path,
        context_path=prob.context_path,
        initial_body_path=prob.initial_body_path,
        stats=RunStats(started_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
    logger.write_inputs(prob.config.model_dump_json(indent=2),
                        prob.informal_problem, prob.context, prob.body_initial,
                        placeholder=prob.placeholder)

    start = _now()
    deadline = start + prob.config.max_runtime_seconds
    def call_timeout():   # bound every LLM call by the remaining budget so no single call blows it
        return max(2.0, min(150.0, deadline - _now()))
    limits = LimitController(prob.config, start)
    failures = FailureController(logger)
    category_counts: dict = {}
    last_error = ""

    def bump(cat): category_counts[cat] = category_counts.get(cat, 0) + 1
    def finish(reason=None):
        state.stats.runtime_seconds = _now() - start
        statemgr.save(state)
        try: verifier.close()        # terminate the warm subprocess if the warm backend is in use
        except Exception: pass
        return state

    # ---- input validation (§4) ----
    validator = InputValidator(verifier)
    try:
        vr = validator.validate(prob)
    except Exception as e:                     # defensive header error etc.
        bump("initial_body_failed")
        failures.write(state, "initial_body_failed", prob.context, prob.body_initial, str(e), category_counts)
        return finish()
    state.formal_theorem_header = vr.header
    state.stats.total_lean_compiles += vr.compile_count
    if not vr.ok:
        bump(vr.reason)
        failures.write(state, vr.reason, prob.context, prob.body_initial,
                       _feedback_from_errors(_check_errs(vr)), category_counts)
        return finish()

    header = vr.header
    prelude = prob.config.lean_prelude
    reasoning = ReasoningAgent(prob.config.reasoning_model, _read(os.path.join(CONSID, "INFORMAL_REASONING_CONSIDERATIONS.md")), reasoning_offline, reasoning_effort=prob.config.reasoning_effort)
    translation = TranslationAgent(prob.config.translation_model, _read(os.path.join(CONSID, "FORMAL_TRANSLATION_CONSIDERATIONS.md")), translation_offline)

    accepted_decls: List[str] = []
    latest_body = prob.body_initial
    informal_progress = ""

    # ---- main loop (§18) ----
    while state.status == "running":
        lim = limits.exceeded(state)
        if lim:
            failures.write(state, lim, _env_text(prelude, prob.context, accepted_decls),
                           latest_body, last_error, category_counts)
            return finish()

        i = len(state.proof_steps) + 1
        ps = ProofStep(proof_step_index=i)
        state.proof_steps.append(ps)
        step_accepted = False
        reasoning_feedback = ""
        failed_next_step = ""

        for j in range(1, prob.config.max_informal_candidates_per_proof_step + 1):
            ic = InformalCandidate(informal_candidate_index=j)
            ps.informal_candidates.append(ic)
            if _now() >= deadline:
                failures.write(state, "max_runtime_seconds", _env_text(prelude, prob.context, accepted_decls),
                               latest_body, last_error, category_counts)
                return finish()
            try:
                r = reasoning.propose(prob.informal_problem, informal_progress, state.current_knowledge,
                                      prob.context, _accepted_summary(accepted_decls), latest_body,
                                      failure_feedback=reasoning_feedback,
                                      failed_next_step=failed_next_step, attempt_index=j - 1,
                                      timeout=call_timeout())
            except Exception as e:                          # timeout / API error — don't crash
                state.stats.total_llm_calls += 1
                last_error = f"reasoning call failed: {type(e).__name__}: {str(e)[:200]}"
                bump("reasoning_call_failed"); ic.status = "abandoned"
                reasoning_feedback = "The previous reasoning attempt did not return; propose a simpler step."
                failed_next_step = ""
                statemgr.save(state); continue
            state.stats.total_llm_calls += 1
            informal_candidate = _extract_informal(r.text)
            logger.write_reasoning(i, j, r.prompt, r.text)
            ic.reasoning_prompt_path = os.path.join(paths.ic(i, j), "reasoning_prompt.md")
            ic.informal_step_path = os.path.join(paths.ic(i, j), "informal_step.md")

            if informal_candidate is None:
                last_error = "reasoning output was missing a NEXT STEP section"
                bump("missing_next_step")
                ic.status = "abandoned"
                reasoning_feedback = (
                    "The previous reasoning output was missing NEXT STEP. Emit one non-empty "
                    "NEXT STEP with its PROOF."
                )
                failed_next_step = ""
                statemgr.save(state)
                continue

            if not informal_candidate:
                last_error = "reasoning output was empty"
                bump("empty_informal_output")
                ic.status = "abandoned"
                reasoning_feedback = (
                    "The previous reasoning output was empty. Emit one non-empty NEXT STEP "
                    "with its PROOF."
                )
                failed_next_step = ""
                statemgr.save(state)
                continue

            candidate_accepted = False
            compiler_feedback = ""
            repair_context = None
            for k in range(1, prob.config.max_lean_translation_attempts_per_candidate + 1):
                lim = limits.exceeded(state)
                if lim:
                    failures.write(state, lim, _env_text(prelude, prob.context, accepted_decls),
                                   latest_body, last_error, category_counts)
                    return finish()

                try:
                    t = translation.translate(header, prob.informal_problem, prelude, prob.context, accepted_decls,
                                              latest_body, informal_candidate,
                                              compiler_feedback=compiler_feedback, repair_context=repair_context, attempt_index=k - 1,
                                              timeout=call_timeout())
                except Exception as e:                      # timeout / API error — don't crash
                    state.stats.total_llm_calls += 1
                    last_error = f"translation call failed: {type(e).__name__}: {str(e)[:200]}"
                    bump("translation_call_failed")
                    compiler_feedback = "The previous translation call did not return; keep the output short."
                    statemgr.save(state); continue
                state.stats.total_llm_calls += 1
                state.stats.total_lean_attempts += 1
                la = LeanTranslationAttempt(lean_translation_attempt_index=k)
                ic.lean_translation_attempts.append(la)

                pr = parse_translator_output(t.text)

                # ---- parse (§11): raw output could not be parsed -> parse_error ----
                if not pr.ok:
                    cj = build_compile_json("parse_error",
                                            _struct(False, [f"parse: {pr.error}"]))
                    _log_attempt(logger, la, paths, i, j, k, t, None, None, cj)
                    la.status = "parse_error"; bump("parse_error")
                    compiler_feedback = f"Your output was not parseable: {pr.error}. Emit the two required sections."
                    repair_context = None
                    statemgr.save(state); continue

                prev_names = _names(accepted_decls)
                sr = check_structure(pr.declarations, pr.body, header, prev_names)
                if not sr.ok:
                    cj = build_compile_json("format_failed", sr)
                    _log_attempt(logger, la, paths, i, j, k, t, pr.declarations, pr.body, cj)
                    la.status = "format_failed"
                    bump("changed_theorem_statement" if any("header" in v for v in sr.violations) else "bad_output_format")
                    compiler_feedback = "Structure check failed: " + "; ".join(sr.violations)
                    repair_context = None
                    statemgr.save(state); continue

                # ---- declaration check then body check (§14) ----
                cp = verifier.check(prelude, prob.context, accepted_decls, pr.declarations, pr.body)
                state.stats.total_lean_compiles += 1
                if not cp.declaration_check.passed:
                    cj = build_compile_json("lemma_failed", sr, cp)
                    _log_attempt(logger, la, paths, i, j, k, t, pr.declarations, pr.body, cj)
                    la.status = "lemma_failed"; bump("new_declaration_does_not_compile")
                    last_error = cp.declaration_raw
                    compiler_feedback = "Declaration failed to compile:\n" + _feedback_from_errors(cj.declaration_check.errors)
                    submitted = _submitted_source(prelude, prob.context, accepted_decls,
                                                  pr.declarations, pr.body,
                                                  prob.config.verifier_backend == "warm")
                    repair_context = TranslationRepairContext(
                        failed_check="declaration_check", raw_output=t.text,
                        declarations=pr.declarations, body=pr.body,
                        diagnostics=_render_repair_diagnostics(
                            cj.declaration_check.errors, submitted, pr.declarations, pr.body))
                    statemgr.save(state); continue

                state.stats.total_lean_compiles += 1
                if not cp.body_check.passed:
                    cj = build_compile_json("body_failed", sr, cp)
                    _log_attempt(logger, la, paths, i, j, k, t, pr.declarations, pr.body, cj)
                    la.status = "body_failed"; bump("body_does_not_compile")
                    last_error = cp.body_raw
                    compiler_feedback = "Body failed to compile:\n" + _feedback_from_errors(cj.body_check.errors)
                    submitted = _submitted_source(prelude, prob.context, accepted_decls,
                                                  pr.declarations, pr.body,
                                                  prob.config.verifier_backend == "warm")
                    repair_context = TranslationRepairContext(
                        failed_check="body_check", raw_output=t.text,
                        declarations=pr.declarations, body=pr.body,
                        diagnostics=_render_repair_diagnostics(
                            cj.body_check.errors, submitted, pr.declarations, pr.body))
                    statemgr.save(state); continue

                # ---- both checks passed ----
                if body_contains_sorry(pr.body):
                    cj = build_compile_json("accepted", sr, cp)
                    _log_attempt(logger, la, paths, i, j, k, t, pr.declarations, pr.body, cj)
                    la.status = "accepted"; ic.status = "accepted"; ps.status = "accepted"
                    logger.copy_accepted(i, pr.declarations, pr.body)
                    accepted_decls.append(pr.declarations); latest_body = pr.body
                    state.stats.accepted_proof_steps += 1
                    knowledge = _extract_next_step(informal_candidate)
                    state.current_knowledge.append(knowledge)
                    informal_progress += f"\n- {knowledge}"
                    candidate_accepted = step_accepted = True
                    statemgr.save(state); break
                else:
                    # ---- final: reconstruct + independent compile (§14 hard rule) ----
                    full = reconstruct(prelude, prob.context, accepted_decls + [pr.declarations], pr.body)
                    final_path = logger.write_final(full, _solution_md(prob, state))
                    fr = verifier.compile_full_file(final_path)
                    state.stats.total_lean_compiles += 1
                    fc = _final_report(fr)
                    if fr.ok and not fr.contains_sorry:
                        cj = build_compile_json("final_success", sr, cp, fc)
                        _log_attempt(logger, la, paths, i, j, k, t, pr.declarations, pr.body, cj)
                        logger.copy_accepted(i, pr.declarations, pr.body)
                        la.status = "final_success"; ic.status = "accepted"; ps.status = "final_success"
                        state.status = "final_success"; state.stats.accepted_proof_steps += 1
                        return finish()
                    else:
                        cj = build_compile_json("final_reconstruction_failed", sr, cp, fc)
                        _log_attempt(logger, la, paths, i, j, k, t, pr.declarations, pr.body, cj)
                        la.status = "final_reconstruction_failed"; bump("final_reconstruction_failed")
                        last_error = fr.raw
                        compiler_feedback = "Reconstructed solution failed to compile:\n" + fr.raw[:800]
                        statemgr.save(state); continue

            if candidate_accepted:
                break
            ic.status = "abandoned"
            failed_next_step = _extract_next_step(informal_candidate)
            reasoning_feedback = (
                "The step above could not be translated after all Lean attempts. Propose a smaller, "
                "more direct, or differently formulated step. Do not repeat it unchanged."
                if failed_next_step else
                "The previous informal candidate could not be translated to Lean. Suggest a simpler "
                "or more direct step."
            )
            statemgr.save(state)

        if state.status == "final_success":
            return finish()
        if not step_accepted:
            ps.status = "failed"
            failures.write(state, "proof_step_failed", _env_text(prelude, prob.context, accepted_decls),
                           latest_body, last_error, category_counts)
            return finish()
        statemgr.save(state)

    return finish()


# ---------- helpers ----------
def _final_report(fr) -> CheckReport:
    from dataclasses import asdict
    ok = fr.ok and not fr.contains_sorry
    return CheckReport(status="passed" if ok else "failed",
                       errors=[Diagnostic(**asdict(d)) for d in fr.errors])

def _names(decls):
    out = []
    for d in decls: out += declared_names(d)
    return out

def _struct(ok, violations):
    from .structure import StructureResult
    return StructureResult(ok=ok, violations=violations)

def _check_errs(vr):
    cp = vr.context_check if (vr.reason == "context_failed") else vr.initial_body_check
    if not cp: return []
    errs = cp.declaration_check.errors + cp.body_check.errors
    from .models import Diagnostic
    out = []
    for e in errs:                       # each is a Diag-as-dict (§19 schema), NOT an object
        out.append(Diagnostic(file=e.get("file", ""), line=int(e.get("line") or 0),
                              col=int(e.get("col") or 0), severity=e.get("severity", "error"),
                              code=e.get("code", ""), message=e.get("message", "")))
    return out

def _env_text(prelude, context, accepted_decls):
    return reconstruct(prelude, context, accepted_decls, "-- (theorem body omitted)")

def _solution_md(prob, state):
    return f"# Solution — {prob.problem_id}\n\nStatus: {state.status}\nAccepted steps: {state.stats.accepted_proof_steps}\n"

def _log_attempt(logger, la, paths, i, j, k, t, decls, body, cj):
    dp, bp, cjp = logger.write_translation(i, j, k, t.prompt, t.text, decls, body, cj)
    la.translator_prompt_path = os.path.join(paths.la(i, j, k), "translator_prompt.md")
    la.raw_translator_output_path = os.path.join(paths.la(i, j, k), "raw_translator_output.md")
    la.declarations_path, la.body_path, la.compile_path = dp, bp, cjp
