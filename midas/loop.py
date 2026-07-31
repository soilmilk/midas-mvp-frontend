"""
The core MVP loop — SPEC.md §18, wiring §21's modules. LimitController (§2/§16) and
FailureController (§16) live here. Entry point: run_problem().
"""
from __future__ import annotations
import os, re, time
from typing import List, Optional

from .models import (ProofRunState, ProofStep, InformalCandidate, LeanTranslationAttempt,
                     RunStats, LLMUsageStats, CheckReport, CompileJson, Diagnostic,
                     attempt_kind_for)
from .problem import load_problem, InputValidator, Problem
from .agents import (LLMResult, OfflineResponse, ReasoningAgent, TranslationAgent,
                     TranslationRepairContext)
from .parser import parse_reasoning_action, parse_translator_output
from .structure import (check_filled_placeholder, check_structure,
                        body_contains_sorry, declared_names)
from .verifier_client import make_verifier, build_compile_json
from .reconstructor import RenderedSource, SourceRegion, render_source
from .artifacts import Paths, LeanArtifactLogger, RunEventLogger, StateManager

CONSID = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "considerations")


def _read(p): return open(p).read()
def _now(): return time.time()


def _account_llm_call(state: ProofRunState, role: str,
                      result: Optional[LLMResult] = None):
    """Record one attempted call without inventing missing provider usage."""
    if role not in ("reasoner", "translator"):
        raise ValueError(f"unsupported LLM role: {role!r}")
    state.stats.total_llm_calls += 1
    buckets = (state.stats.llm_usage.total,
               getattr(state.stats.llm_usage, role))
    for bucket in buckets:
        bucket.calls += 1
        if result is None:
            continue
        if result.prompt_tokens is not None or result.completion_tokens is not None:
            bucket.calls_with_token_usage += 1
        if result.cost_credits is not None:
            bucket.calls_with_cost += 1
        for field in ("prompt_tokens", "completion_tokens", "total_tokens",
                      "reasoning_tokens", "cached_tokens"):
            value = getattr(result, field)
            if value is not None:
                setattr(bucket, field, getattr(bucket, field) + int(value))
        if result.cost_credits is not None:
            bucket.cost_credits += float(result.cost_credits)


def _usage_event(role: str, result: LLMResult) -> str:
    def shown(value):
        return "unavailable" if value is None else str(value)
    return (
        f"{role.capitalize()} usage: model={result.model}, "
        f"prompt_tokens={shown(result.prompt_tokens)}, "
        f"completion_tokens={shown(result.completion_tokens)}, "
        f"total_tokens={shown(result.total_tokens)}, "
        f"reasoning_tokens={shown(result.reasoning_tokens)}, "
        f"cached_tokens={shown(result.cached_tokens)}, "
        f"cost_credits={shown(result.cost_credits)}"
    )


def _usage_totals(stats: LLMUsageStats) -> str:
    return (
        f"accounted_token_calls={stats.calls_with_token_usage}/{stats.calls}, "
        f"accounted_cost_calls={stats.calls_with_cost}/{stats.calls}, "
        f"prompt_tokens={stats.prompt_tokens}, "
        f"completion_tokens={stats.completion_tokens}, "
        f"total_tokens={stats.total_tokens}, "
        f"reasoning_tokens={stats.reasoning_tokens}, "
        f"cached_tokens={stats.cached_tokens}, "
        f"cost_credits={stats.cost_credits:.10g}"
    )


def _feedback_from_errors(errs: List[Diagnostic]) -> str:
    return "\n".join(f"{e.file}:{e.line}:{e.col}: {e.severity}"
                     f"{'('+e.code+')' if e.code else ''}: {e.message}" for e in errs) or "(no detail)"


def _diagnostics(errors) -> List[Diagnostic]:
    out = []
    for error in errors or []:
        if isinstance(error, Diagnostic):
            out.append(error)
        else:
            out.append(Diagnostic(
                file=error.get("file", ""),
                line=int(error.get("line") or 0),
                col=int(error.get("col") or 0),
                severity=error.get("severity", "error"),
                code=error.get("code", ""),
                message=error.get("message", ""),
            ))
    return out


def _submitted_rendered_source(
    prelude: List[str],
    context: str,
    accepted_decls: List[str],
    declarations: str,
    body: str,
    warm: bool,
    placeholder: str = "",
    include_suffix: bool = True,
) -> RenderedSource:
    """Reproduce the source layout used by the selected verifier for location mapping."""
    submitted_prelude = ([line for line in (prelude or [])
                          if not line.strip().startswith("import ")]
                         if warm else prelude)
    return render_source(
        submitted_prelude,
        context,
        accepted_decls,
        candidate_declarations=declarations,
        placeholder=placeholder if include_suffix else "",
        theorem_body=body if include_suffix else "",
    )


def _submitted_source(prelude: List[str], context: str, accepted_decls: List[str],
                      declarations: str, body: str, warm: bool,
                      placeholder: str = "") -> str:
    """Compatibility wrapper retained for existing diagnostic tests."""
    return _submitted_rendered_source(
        prelude, context, accepted_decls, declarations, body, warm, placeholder
    ).text


def _effective_location(error: Diagnostic) -> tuple[int, int]:
    line, col = error.line, error.col
    if line <= 0:
        embedded = re.search(r"<req>:(\d+):(\d+):", error.message)
        if embedded:
            line, col = int(embedded.group(1)), int(embedded.group(2))
    return line, col


def _region_label(region_name: str, attempt_kind: str) -> str:
    body_heading = (
        "UPDATED THEOREM BODY"
        if attempt_kind == "exploration"
        else "FINAL THEOREM BODY"
    )
    if region_name == "candidate_declarations":
        return "rejected NEW DECLARATIONS"
    if region_name == "placeholder":
        return (
            "rejected FILLED PLACEHOLDER"
            if attempt_kind == "hard_finalization"
            else "unresolved PLACEHOLDER"
        )
    if region_name == "theorem_body":
        return f"rejected {body_heading}"
    return "prelude, context, or previously accepted code"


def _region_for_line(rendered: RenderedSource, line: int,
                     attempt_kind: str) -> str:
    for region in rendered.regions:
        if region.start_line <= line <= region.end_line:
            return _region_label(region.name, attempt_kind)
    return "prelude, context, or previously accepted code"


def _numbered_excerpt(rendered: RenderedSource, line: int, col: int) -> str:
    lines = rendered.text.splitlines()
    if line <= 0 or not lines:
        return ""
    lo, hi = max(1, line - 3), min(len(lines), line + 3)
    width = len(str(hi))
    excerpt = [f"{n:>{width}} | {lines[n - 1]}" for n in range(lo, hi + 1)]
    if line <= len(lines):
        excerpt.append(" " * width + " | " + " " * max(0, col) + "^")
    return "\n".join(excerpt)


def _annotate_report(report: CheckReport, rendered: RenderedSource,
                     attempt_kind: str) -> None:
    """Persist the exact source region and excerpt used in repair prompts."""
    for error in report.errors:
        line, col = _effective_location(error)
        error.source_region = _region_for_line(rendered, line, attempt_kind)
        error.nearby_code = _numbered_excerpt(rendered, line, col)


def _errors_in_region(errors: List[Diagnostic], rendered: RenderedSource,
                      region_name: str) -> bool:
    targets = [region for region in rendered.regions if region.name == region_name]
    return any(
        any(region.start_line <= _effective_location(error)[0] <= region.end_line
            for region in targets)
        for error in errors
    )


def _render_repair_diagnostics(errors: List[Diagnostic],
                               rendered_source,
                               declarations: str = "",
                               body: str = "",
                               placeholder: str = "",
                               attempt_kind: str = "exploration") -> str:
    """Render every diagnostic from the verifier's authoritative source spans."""
    if isinstance(rendered_source, str):
        # Compatibility for older direct callers. Production paths pass the
        # RenderedSource produced by the verifier's shared renderer.
        regions = []
        search_from = 0
        for name, fragment in (
            ("candidate_declarations", declarations),
            ("placeholder", placeholder),
            ("theorem_body", body),
        ):
            if not fragment.strip():
                continue
            pos = rendered_source.find(fragment.rstrip(), search_from)
            if pos < 0:
                continue
            start = rendered_source.count("\n", 0, pos) + 1
            regions.append(SourceRegion(
                name, start, start + fragment.rstrip().count("\n")
            ))
            search_from = pos + len(fragment.rstrip())
        rendered_source = RenderedSource(rendered_source, regions)

    items = []
    for index, error in enumerate(errors, 1):
        line, col = _effective_location(error)
        region = error.source_region or _region_for_line(
            rendered_source, line, attempt_kind
        )
        code = f" ({error.code})" if error.code else ""
        item = [f"#### Error {index}",
                f"- Diagnostic: `{error.file}:{line}:{col}` {error.severity}{code}",
                f"- Source region: **{region}**",
                f"- Message: {error.message}"]
        excerpt = error.nearby_code or _numbered_excerpt(
            rendered_source, line, col
        )
        if excerpt:
            item.extend(["- Nearby submitted Lean code:", "```lean4",
                         excerpt, "```"])
        else:
            item.append("- Nearby submitted Lean code: unavailable because the verifier supplied no line number.")
        items.append("\n".join(item))
    return "\n\n".join(items) if items else "No structured diagnostics were returned."


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
                reasoning_offline: Optional[List[OfflineResponse]] = None,
                translation_offline: Optional[List[OfflineResponse]] = None) -> ProofRunState:
    problem_dir = os.path.abspath(problem_dir)
    problem_id = os.path.basename(problem_dir.rstrip("/"))
    runs_root = runs_root or os.path.join(problem_dir, "runs")
    paths = Paths(runs_root, problem_id)
    events = RunEventLogger(paths)
    events.event(f"Run started: {problem_id}", console=True, elapsed_ms=0)
    events.event(f"Problem directory: {problem_dir}", indent=1)
    events.event(f"Run directory: {paths.root}", indent=1)
    try:
        prob = load_problem(problem_dir)
    except Exception as error:
        events.event(
            f"Problem loading failed: {type(error).__name__}: {error}",
            indent=1, console=True,
        )
        events.close()
        raise
    logger = LeanArtifactLogger(paths)
    statemgr = StateManager(paths)
    events.event(
        f"Configuration: mode={prob.config.problem_mode}, "
        f"reasoner={prob.config.reasoning_model}, "
        f"translator={prob.config.translation_model}, "
        f"verifier={prob.config.verifier_backend}",
        indent=1,
    )
    events.event(
        f"Limits: proof_steps={prob.config.max_proof_steps}, "
        f"candidates_per_step={prob.config.max_informal_candidates_per_proof_step}, "
        f"translations_per_candidate={prob.config.max_lean_translation_attempts_per_candidate}, "
        f"lean_attempts={prob.config.max_total_lean_attempts}, "
        f"runtime_seconds={prob.config.max_runtime_seconds}",
        indent=1,
    )
    events.event(
        f"Initializing {prob.config.verifier_backend} Lean 4 verifier",
        indent=1, console=True,
    )
    try:
        verifier = make_verifier(prob.config)   # fresh process or warm transaction server
    except Exception as error:
        events.event(
            f"Verifier initialization failed: {type(error).__name__}: {error}",
            indent=1, console=True,
        )
        events.close()
        raise

    state = ProofRunState(
        problem_id=prob.problem_id,
        informal_problem_path=prob.informal_problem_path,
        context_path=prob.context_path,
        initial_body_path=prob.initial_body_path,
        problem_mode=prob.config.problem_mode,
        placeholder_path=prob.placeholder_path,
        placeholder_initial_source=prob.placeholder,
        placeholder_status=(
            "unresolved" if prob.config.problem_mode == "hard" else None
        ),
        stats=RunStats(started_at=time.strftime("%Y-%m-%dT%H:%M:%S")))
    logger.write_inputs(prob.config.model_dump_json(indent=2),
                        prob.informal_problem, prob.context, prob.body_initial,
                        placeholder=prob.placeholder)
    statemgr.save(state)

    start = _now()
    deadline = start + prob.config.max_runtime_seconds
    def call_timeout():   # bound every LLM call by the remaining budget so no single call blows it
        return max(2.0, min(150.0, deadline - _now()))
    limits = LimitController(prob.config, start)
    failures = FailureController(logger)
    category_counts: dict = {}
    last_error = ""

    def bump(cat): category_counts[cat] = category_counts.get(cat, 0) + 1
    def log_diagnostics(errors, indent):
        for error in _diagnostics(errors):
            location = f"{error.file}:{error.line}:{error.col}"
            code = f" ({error.code})" if error.code else ""
            events.event(
                f"{error.severity}{code} at {location}: {error.message}",
                indent=indent,
            )
    def finish(reason=None):
        state.stats.runtime_seconds = _now() - start
        statemgr.save(state)
        events.event(
            f"Run finished: status={state.status}"
            + (f", reason={state.failure_reason}" if state.failure_reason else ""),
            console=True,
        )
        events.event(
            f"Totals: accepted_steps={state.stats.accepted_proof_steps}, "
            f"llm_calls={state.stats.total_llm_calls}, "
            f"lean_compiles={state.stats.total_lean_compiles}, "
            f"lean_attempts={state.stats.total_lean_attempts}, "
            f"runtime_seconds={state.stats.runtime_seconds:.3f}, "
            + _usage_totals(state.stats.llm_usage.total),
            indent=1,
        )
        events.event("Closing Lean 4 verifier", indent=1)
        try: verifier.close()        # terminate the warm subprocess if the warm backend is in use
        except Exception: pass
        events.close()
        return state

    # ---- input validation (§4) ----
    events.event("Input validation started", indent=1, console=True)
    events.event(
        "Compiling Lean 4 input checkpoint (context and initial theorem body)",
        indent=2, console=True,
    )
    validation_started = _now()
    validator = InputValidator(verifier)
    try:
        vr = validator.validate(prob)
    except Exception as e:                     # defensive header error etc.
        events.event(
            f"Input validation raised {type(e).__name__} after "
            f"{_now() - validation_started:.3f}s: {e}",
            indent=2, console=True,
        )
        bump("initial_body_failed")
        failures.write(state, "initial_body_failed", prob.context, prob.body_initial, str(e), category_counts)
        return finish()
    state.formal_theorem_header = vr.header
    state.placeholder_header = vr.placeholder_header or None
    state.placeholder_name = vr.placeholder_name or None
    state.stats.total_lean_compiles += vr.compile_count
    events.event(
        f"Input validation {'passed' if vr.ok else 'failed'} after "
        f"{_now() - validation_started:.3f}s; lean_compiles={vr.compile_count}"
        + (f", reason={vr.reason}" if vr.reason else ""),
        indent=2, console=True,
    )
    if vr.context_check is not None:
        context_status = (
            "accepted"
            if vr.context_check.declaration_check.passed else "context_failed"
        )
        logger.write_input_check(
            "context_check.json",
            build_compile_json(
                context_status, _struct(True, []), vr.context_check
            ),
        )
        initial_status = (
            "accepted"
            if vr.context_check.declaration_check.passed
            and vr.context_check.body_check.passed
            else vr.reason
        )
        logger.write_input_check(
            "initial_body_check.json",
            build_compile_json(
                initial_status, _struct(True, []), vr.initial_body_check
            ),
        )
        statemgr.save(state)
    if not vr.ok:
        log_diagnostics(_check_errs(vr), 3)
        bump(vr.reason)
        failures.write(state, vr.reason, prob.context, prob.body_initial,
                       _feedback_from_errors(_check_errs(vr)), category_counts)
        return finish()

    header = vr.header
    problem_mode = prob.config.problem_mode
    placeholder_initial_source = vr.placeholder_initial_source
    placeholder_header = vr.placeholder_header
    placeholder_name = vr.placeholder_name
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
            events.event(f"Run limit reached: {lim}", indent=1, console=True)
            failures.write(state, lim, _env_text(
                               prelude, prob.context, accepted_decls,
                               placeholder_initial_source
                               if problem_mode == "hard" else ""),
                           latest_body, last_error, category_counts)
            return finish()

        i = len(state.proof_steps) + 1
        ps = ProofStep(proof_step_index=i)
        state.proof_steps.append(ps)
        events.event(f"Proof step {i} started", indent=1, console=True)
        step_accepted = False
        reasoning_feedback = ""
        failed_next_step = ""

        for j in range(1, prob.config.max_informal_candidates_per_proof_step + 1):
            ic = InformalCandidate(informal_candidate_index=j)
            ps.informal_candidates.append(ic)
            events.event(f"Candidate {j} started", indent=2, console=True)
            statemgr.save(state)
            if _now() >= deadline:
                events.event(
                    "Run limit reached: max_runtime_seconds",
                    indent=2, console=True,
                )
                failures.write(state, "max_runtime_seconds", _env_text(
                                   prelude, prob.context, accepted_decls,
                                   placeholder_initial_source
                                   if problem_mode == "hard" else ""),
                               latest_body, last_error, category_counts)
                return finish()
            reasoning_prompt = reasoning.build_prompt(
                prob.informal_problem,
                informal_progress,
                problem_mode=prob.config.problem_mode,
                failure_feedback=reasoning_feedback,
                failed_next_step=failed_next_step,
            )
            ic.reasoning_prompt_path = logger.write_reasoning_prompt(
                i, j, reasoning_prompt
            )
            events.event(
                f"Reasoning prompt: {ic.reasoning_prompt_path} "
                f"({len(reasoning_prompt)} chars)",
                indent=3,
            )
            statemgr.save(state)
            reasoning_timeout = call_timeout()
            events.event(
                f"Waiting for reasoner response (model={reasoning.model}, "
                f"timeout={reasoning_timeout:.1f}s)",
                indent=3, console=True,
            )
            reasoning_started = _now()
            try:
                r = reasoning.propose(
                    attempt_index=j - 1,
                    timeout=reasoning_timeout,
                    prepared_prompt=reasoning_prompt,
                )
            except Exception as e:                          # timeout / API error — don't crash
                _account_llm_call(state, "reasoner")
                last_error = f"reasoning call failed: {type(e).__name__}: {str(e)[:200]}"
                bump("reasoning_call_failed"); ic.status = "abandoned"
                ic.informal_step_path = logger.write_reasoning_output(i, j, "")
                ic.reasoning_call_error_path = logger.write_reasoning_error(
                    i, j, last_error
                )
                events.event(
                    f"Reasoner call failed after {_now() - reasoning_started:.3f}s: "
                    f"{type(e).__name__}: {str(e)[:200]}",
                    indent=3, console=True,
                )
                reasoning_feedback = "The previous reasoning attempt did not return; propose a simpler step."
                failed_next_step = ""
                statemgr.save(state); continue
            _account_llm_call(state, "reasoner", r)
            ic.informal_step_path = logger.write_reasoning_output(i, j, r.text)
            events.event(
                f"Reasoner response received after {_now() - reasoning_started:.3f}s "
                f"({len(r.text)} chars)",
                indent=3, console=True,
            )
            events.event(
                f"Reasoner response artifact: {ic.informal_step_path}",
                indent=4,
            )
            events.event(_usage_event("reasoner", r), indent=4)
            statemgr.save(state)
            action = parse_reasoning_action(r.text, prob.config.problem_mode)

            if not action.ok:
                last_error = f"invalid reasoner action: {action.error}"
                bump("invalid_reasoner_action")
                ic.status = "abandoned"
                reasoning_feedback = (
                    "The previous response was not a valid action: "
                    f"{action.error}. Return one action with non-empty NEXT STEP and PROOF, "
                    "and an exact IS_FINAL_STEP Boolean."
                )
                failed_next_step = ""
                events.event(
                    f"Reasoner action rejected: {action.error}",
                    indent=3, console=True,
                )
                statemgr.save(state)
                continue

            informal_candidate = action.informal_step
            attempt_kind = attempt_kind_for(
                problem_mode, bool(action.is_final_step)
            )
            events.event(
                f"Reasoner action accepted: kind={attempt_kind}, "
                f"next_step={action.next_step[:200]!r}",
                indent=3,
            )
            candidate_accepted = False
            compiler_feedback = ""
            repair_context = None
            for k in range(1, prob.config.max_lean_translation_attempts_per_candidate + 1):
                lim = limits.exceeded(state)
                if lim:
                    events.event(f"Run limit reached: {lim}", indent=3, console=True)
                    failures.write(state, lim, _env_text(
                                       prelude, prob.context, accepted_decls,
                                       placeholder_initial_source
                                       if problem_mode == "hard" else ""),
                                   latest_body, last_error, category_counts)
                    return finish()

                state.stats.total_lean_attempts += 1
                la = LeanTranslationAttempt(
                    lean_translation_attempt_index=k,
                    attempt_kind=attempt_kind,
                    proposed_final_answer=(
                        action.answer if attempt_kind == "hard_finalization" else None
                    ),
                )
                ic.lean_translation_attempts.append(la)
                events.event(
                    f"Lean translation attempt {k} started (kind={attempt_kind})",
                    indent=3, console=True,
                )
                statemgr.save(state)
                translation_prompt = translation.build_prompt(
                    header, prob.informal_problem, prelude, prob.context,
                    accepted_decls, latest_body, informal_candidate,
                    compiler_feedback=compiler_feedback,
                    repair_context=repair_context,
                    attempt_kind=attempt_kind,
                    placeholder_header=(
                        placeholder_header if problem_mode == "hard" else None
                    ),
                    placeholder_initial_source=(
                        placeholder_initial_source
                        if problem_mode == "hard" else None
                    ),
                    answer=action.answer,
                )
                la.translator_prompt_path = logger.write_translation_prompt(
                    i, j, k, translation_prompt
                )
                events.event(
                    f"Translator prompt: {la.translator_prompt_path} "
                    f"({len(translation_prompt)} chars)",
                    indent=4,
                )
                statemgr.save(state)
                translation_timeout = call_timeout()
                events.event(
                    f"Waiting for translator response (model={translation.model}, "
                    f"timeout={translation_timeout:.1f}s)",
                    indent=4, console=True,
                )
                translation_started = _now()
                try:
                    t = translation.translate(
                        attempt_index=k - 1,
                        timeout=translation_timeout,
                        prepared_prompt=translation_prompt,
                    )
                except Exception as e:                      # timeout / API error — don't crash
                    _account_llm_call(state, "translator")
                    last_error = f"translation call failed: {type(e).__name__}: {str(e)[:200]}"
                    bump("translation_call_failed")
                    la.status = "translation_call_failed"
                    cj = CompileJson(
                        attempt_status="translation_call_failed",
                        attempt_kind=attempt_kind,
                        translator_output_empty=True,
                        translation_call_error=last_error,
                    )
                    failed_result = LLMResult(
                        prompt=translation.last_prompt,
                        text="",
                        model=translation.model,
                    )
                    _log_attempt(
                        logger, la, paths, i, j, k, failed_result,
                        None, None, cj,
                    )
                    events.event(
                        f"Translator call failed after {_now() - translation_started:.3f}s: "
                        f"{type(e).__name__}: {str(e)[:200]}",
                        indent=4, console=True,
                    )
                    compiler_feedback = "The previous translation call did not return; keep the output short."
                    statemgr.save(state); continue
                _account_llm_call(state, "translator", t)
                la.raw_translator_output_path = logger.write_translation_output(
                    i, j, k, t.text
                )
                events.event(
                    f"Translator response received after {_now() - translation_started:.3f}s "
                    f"({len(t.text)} chars)",
                    indent=4, console=True,
                )
                events.event(
                    f"Translator response artifact: {la.raw_translator_output_path}",
                    indent=5,
                )
                events.event(_usage_event("translator", t), indent=5)
                statemgr.save(state)

                pr = parse_translator_output(t.text, attempt_kind)

                # ---- parse (§11): raw output could not be parsed -> parse_error ----
                if not pr.ok:
                    cj = build_compile_json(
                        "parse_error",
                        _struct(False, [f"parse: {pr.error}"]),
                        attempt_kind=attempt_kind,
                    )
                    _log_attempt(
                        logger, la, paths, i, j, k, t, None, None, cj
                    )
                    la.status = "parse_error"; bump("parse_error")
                    compiler_feedback = (
                        f"Your output was not parseable: {pr.error}. "
                        "Emit the complete required transaction."
                    )
                    repair_context = None
                    events.event(
                        f"Translator output parse failed: {pr.error}; retrying",
                        indent=4, console=True,
                    )
                    statemgr.save(state); continue

                dp, pp, bp = logger.write_parsed_translation(
                    i, j, k, pr.declarations, pr.body,
                    placeholder=pr.placeholder,
                )
                la.declarations_path = dp
                la.placeholder_path = pp
                la.body_path = bp
                statemgr.save(state)

                prev_names = _names(accepted_decls)
                sr = check_structure(pr.declarations, pr.body, header, prev_names)
                if attempt_kind != "exploration" and body_contains_sorry(pr.body):
                    sr.violations.append("final theorem body contains `sorry`")
                    sr.ok = False
                if attempt_kind == "hard_finalization":
                    placeholder_sr = check_filled_placeholder(
                        pr.placeholder or "", placeholder_header, placeholder_name
                    )
                    sr.violations.extend(placeholder_sr.violations)
                    sr.ok = sr.ok and placeholder_sr.ok
                if not sr.ok:
                    status = (
                        "placeholder_format_failed"
                        if attempt_kind == "hard_finalization"
                        and any("placeholder" in v for v in sr.violations)
                        else "format_failed"
                    )
                    cj = build_compile_json(
                        status, sr, attempt_kind=attempt_kind
                    )
                    _log_attempt(
                        logger, la, paths, i, j, k, t,
                        pr.declarations, pr.body, cj,
                        placeholder=pr.placeholder,
                    )
                    la.status = status
                    if status == "placeholder_format_failed":
                        bump("placeholder_format_failed")
                    else:
                        bump("changed_theorem_statement"
                             if any("header" in v for v in sr.violations)
                             else "bad_output_format")
                    structure_feedback = (
                        "Structure check failed:\n"
                        + "\n".join(f"- {v}" for v in sr.violations)
                    )
                    compiler_feedback = structure_feedback
                    repair_context = TranslationRepairContext(
                        failed_check="structure_check",
                        raw_output=t.text,
                        declarations=pr.declarations or "",
                        placeholder=pr.placeholder or "",
                        body=pr.body or "",
                        diagnostics=structure_feedback,
                        attempt_kind=attempt_kind,
                    )
                    events.event(
                        f"Structure check failed ({status}); retrying",
                        indent=4, console=True,
                    )
                    for violation in sr.violations:
                        events.event(violation, indent=5)
                    statemgr.save(state); continue

                events.event("Structure check passed", indent=4)

                # ---- declaration check then body check (§14) ----
                candidate_placeholder = (
                    placeholder_initial_source
                    if attempt_kind == "exploration" and problem_mode == "hard"
                    else (pr.placeholder or "")
                    if attempt_kind == "hard_finalization"
                    else ""
                )
                declaration_input = _submitted_rendered_source(
                    prelude, prob.context, accepted_decls,
                    pr.declarations, pr.body,
                    prob.config.verifier_backend == "warm",
                    candidate_placeholder,
                    include_suffix=False,
                )
                body_input = _submitted_rendered_source(
                    prelude, prob.context, accepted_decls,
                    pr.declarations, pr.body,
                    prob.config.verifier_backend == "warm",
                    candidate_placeholder,
                )
                la.declaration_check_input_path = logger.write_attempt_source(
                    i, j, k, "declaration_check_input.lean",
                    declaration_input.text,
                )
                la.body_check_input_path = logger.write_attempt_source(
                    i, j, k, "body_check_input.lean", body_input.text
                )
                events.event(
                    f"Lean inputs: declarations={la.declaration_check_input_path}; "
                    f"body={la.body_check_input_path}",
                    indent=5,
                )
                statemgr.save(state)
                events.event(
                    "Compiling Lean 4 checkpoint (candidate declarations, then theorem body)",
                    indent=4, console=True,
                )
                compile_started = _now()
                cp = verifier.check(
                    prelude,
                    prob.context,
                    accepted_decls,
                    pr.declarations,
                    pr.body,
                    placeholder=candidate_placeholder,
                    require_closed=(attempt_kind != "exploration"),
                )
                state.stats.total_lean_compiles += 1
                if not cp.declaration_check.passed:
                    submitted = _submitted_rendered_source(
                        prelude, prob.context, accepted_decls,
                        pr.declarations, pr.body,
                        prob.config.verifier_backend == "warm",
                        candidate_placeholder,
                        include_suffix=False,
                    )
                    cj = build_compile_json(
                        "lemma_failed", sr, cp, attempt_kind=attempt_kind
                    )
                    _annotate_report(
                        cj.declaration_check, submitted, attempt_kind
                    )
                    _log_attempt(
                        logger, la, paths, i, j, k, t,
                        pr.declarations, pr.body, cj,
                        placeholder=pr.placeholder,
                    )
                    la.status = "lemma_failed"; bump("new_declaration_does_not_compile")
                    last_error = cp.declaration_raw
                    compiler_feedback = "Declaration failed to compile:\n" + _feedback_from_errors(cj.declaration_check.errors)
                    repair_context = TranslationRepairContext(
                        failed_check="declaration_check", raw_output=t.text,
                        declarations=pr.declarations, body=pr.body,
                        placeholder=pr.placeholder or "",
                        attempt_kind=attempt_kind,
                        diagnostics=_render_repair_diagnostics(
                            cj.declaration_check.errors, submitted,
                            attempt_kind=attempt_kind))
                    events.event(
                        f"Lean 4 declaration check failed after "
                        f"{_now() - compile_started:.3f}s; retrying",
                        indent=4, console=True,
                    )
                    log_diagnostics(cj.declaration_check.errors, 5)
                    statemgr.save(state); continue

                state.stats.total_lean_compiles += 1
                if not cp.body_check.passed:
                    submitted = _submitted_rendered_source(
                        prelude, prob.context, accepted_decls,
                        pr.declarations, pr.body,
                        prob.config.verifier_backend == "warm",
                        candidate_placeholder,
                    )
                    status = (
                        "placeholder_fill_failed"
                        if attempt_kind == "hard_finalization"
                        and _errors_in_region(
                            _diagnostics(cp.body_check.errors),
                            submitted,
                            "placeholder",
                        )
                        else "body_failed"
                    )
                    cj = build_compile_json(
                        status, sr, cp, attempt_kind=attempt_kind
                    )
                    _annotate_report(cj.body_check, submitted, attempt_kind)
                    _log_attempt(
                        logger, la, paths, i, j, k, t,
                        pr.declarations, pr.body, cj,
                        placeholder=pr.placeholder,
                    )
                    la.status = status
                    bump(status if status == "placeholder_fill_failed"
                         else "body_does_not_compile")
                    last_error = cp.body_raw
                    compiler_feedback = (
                        "Finalization suffix failed to compile:\n"
                        if attempt_kind != "exploration"
                        else "Body failed to compile:\n"
                    ) + _feedback_from_errors(cj.body_check.errors)
                    repair_context = TranslationRepairContext(
                        failed_check="body_check", raw_output=t.text,
                        declarations=pr.declarations, body=pr.body,
                        placeholder=pr.placeholder or "",
                        attempt_kind=attempt_kind,
                        diagnostics=_render_repair_diagnostics(
                            cj.body_check.errors, submitted,
                            attempt_kind=attempt_kind))
                    events.event(
                        f"Lean 4 theorem-body check failed after "
                        f"{_now() - compile_started:.3f}s ({status}); retrying",
                        indent=4, console=True,
                    )
                    log_diagnostics(cj.body_check.errors, 5)
                    statemgr.save(state); continue

                events.event(
                    f"Lean 4 checkpoint passed after {_now() - compile_started:.3f}s",
                    indent=4, console=True,
                )

                # ---- both checks passed ----
                if attempt_kind == "exploration":
                    cj = build_compile_json(
                        "accepted", sr, cp, attempt_kind=attempt_kind
                    )
                    _log_attempt(
                        logger, la, paths, i, j, k, t,
                        pr.declarations, pr.body, cj,
                    )
                    la.status = "accepted"; ic.status = "accepted"; ps.status = "accepted"
                    logger.copy_accepted(i, pr.declarations, pr.body)
                    accepted_decls.append(pr.declarations); latest_body = pr.body
                    state.stats.accepted_proof_steps += 1
                    knowledge = action.next_step
                    state.current_knowledge.append(knowledge)
                    informal_progress += f"\n- {knowledge}"
                    candidate_accepted = step_accepted = True
                    events.event(
                        f"Proof step {i} accepted from candidate {j}, attempt {k}",
                        indent=2, console=True,
                    )
                    statemgr.save(state); break
                else:
                    # Final proposals remain non-authoritative until this exact
                    # reconstruction compiles independently without `sorry`.
                    final_rendered = render_source(
                        prelude,
                        prob.context,
                        accepted_decls,
                        candidate_declarations=pr.declarations,
                        placeholder=(
                            pr.placeholder or ""
                            if attempt_kind == "hard_finalization"
                            else ""
                        ),
                        theorem_body=pr.body,
                    )
                    full = final_rendered.text
                    la.final_check_input_path = logger.write_attempt_source(
                        i, j, k, "final_check_input.lean", full
                    )
                    events.event(
                        f"Final reconstruction input: {la.final_check_input_path}",
                        indent=5,
                    )
                    statemgr.save(state)
                    candidate_path = logger.write_final_candidate(full)
                    events.event(
                        "Compiling independent final Lean 4 reconstruction",
                        indent=4, console=True,
                    )
                    final_compile_started = _now()
                    try:
                        fr = verifier.compile_full_file(candidate_path)
                    finally:
                        try:
                            os.unlink(candidate_path)
                        except OSError:
                            pass
                    state.stats.total_lean_compiles += 1
                    fc = _final_report(fr)
                    _annotate_report(fc, final_rendered, attempt_kind)
                    if fr.ok and not fr.contains_sorry:
                        events.event(
                            f"Final Lean 4 reconstruction passed after "
                            f"{_now() - final_compile_started:.3f}s",
                            indent=4, console=True,
                        )
                        cj = build_compile_json(
                            "final_success", sr, cp, fc,
                            attempt_kind=attempt_kind,
                        )
                        _log_attempt(
                            logger, la, paths, i, j, k, t,
                            pr.declarations, pr.body, cj,
                            placeholder=pr.placeholder,
                        )
                        logger.copy_accepted(
                            i, pr.declarations, pr.body,
                            placeholder=pr.placeholder,
                        )
                        logger.write_final(
                            full,
                            _solution_md(prob, state, "final_success"),
                            placeholder=pr.placeholder,
                            body=pr.body,
                        )
                        accepted_decls.append(pr.declarations)
                        latest_body = pr.body
                        if attempt_kind == "hard_finalization":
                            state.placeholder_final_source = pr.placeholder
                            state.placeholder_status = "filled"
                        la.status = "final_success"; ic.status = "accepted"; ps.status = "final_success"
                        state.status = "final_success"; state.stats.accepted_proof_steps += 1
                        return finish()
                    else:
                        status = (
                            "hard_full_reconstruction_failed"
                            if attempt_kind == "hard_finalization"
                            else "final_reconstruction_failed"
                        )
                        cj = build_compile_json(
                            status, sr, cp, fc, attempt_kind=attempt_kind
                        )
                        _log_attempt(
                            logger, la, paths, i, j, k, t,
                            pr.declarations, pr.body, cj,
                            placeholder=pr.placeholder,
                        )
                        la.status = status; bump(status)
                        last_error = fr.raw
                        compiler_feedback = "Reconstructed solution failed to compile:\n" + fr.raw[:800]
                        repair_context = TranslationRepairContext(
                            failed_check="final_check",
                            raw_output=t.text,
                            declarations=pr.declarations,
                            placeholder=pr.placeholder or "",
                            body=pr.body,
                            diagnostics=_render_repair_diagnostics(
                                fc.errors, final_rendered,
                                attempt_kind=attempt_kind,
                            ),
                            attempt_kind=attempt_kind,
                        )
                        events.event(
                            f"Final Lean 4 reconstruction failed after "
                            f"{_now() - final_compile_started:.3f}s; retrying",
                            indent=4, console=True,
                        )
                        log_diagnostics(fc.errors, 5)
                        statemgr.save(state); continue

            if candidate_accepted:
                break
            ic.status = "abandoned"
            events.event(
                f"Candidate {j} abandoned after exhausting translation attempts",
                indent=2, console=True,
            )
            failed_next_step = action.next_step
            reasoning_feedback = (
                "The step above could not be verified due to being too complex or incorrect. Propose a smaller, "
                "more direct, or differently formulated step that is translatable to Lean 4. Do not repeat it unchanged."
                if failed_next_step else
                "The previous step could not be verified due to being too complex or incorrect. Propose a smaller, "
                "more direct, or differently formulated step that is translatable to Lean 4. Do not repeat it unchanged."
            )
            statemgr.save(state)

        if state.status == "final_success":
            return finish()
        if not step_accepted:
            ps.status = "failed"
            events.event(
                f"Proof step {i} failed: all candidates exhausted",
                indent=1, console=True,
            )
            failures.write(state, "proof_step_failed", _env_text(
                               prelude, prob.context, accepted_decls,
                               placeholder_initial_source
                               if problem_mode == "hard" else ""),
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

def _env_text(prelude, context, accepted_decls, placeholder=""):
    return render_source(
        prelude,
        context,
        accepted_decls,
        placeholder=placeholder,
        theorem_body="-- (theorem body omitted)",
    ).text

def _solution_md(prob, state, status=None):
    return (
        f"# Solution — {prob.problem_id}\n\n"
        f"Status: {status or state.status}\n"
        f"Accepted steps: {state.stats.accepted_proof_steps + 1}\n"
    )

def _log_attempt(logger, la, paths, i, j, k, t, decls, body, cj,
                 placeholder=None):
    dp, pp, bp, cjp = logger.write_translation(
        i, j, k, t.prompt, t.text, decls, body, cj,
        placeholder=placeholder,
    )
    la.translator_prompt_path = os.path.join(paths.la(i, j, k), "translator_prompt.md")
    la.raw_translator_output_path = os.path.join(paths.la(i, j, k), "raw_translator_output.md")
    la.declarations_path = dp
    la.placeholder_path = pp
    la.body_path = bp
    la.compile_path = cjp
