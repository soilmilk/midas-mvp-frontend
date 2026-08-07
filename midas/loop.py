"""
The core MVP loop — SPEC.md §18, wiring §21's modules. LimitController (§2/§16) and
FailureController (§16) live here. Entry point: run_problem().
"""
from __future__ import annotations
import json, os, re, shutil, time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

from .models import (AcceptedKnowledge, ProofRunState, ProofStep, InformalCandidate, LeanTranslationAttempt,
                     SemanticReviewAttempt, SemanticReviewReport,
                     RunStats, LLMUsageStats, CheckReport, CompileJson, Diagnostic,
                     Config, attempt_kind_for)
from .problem import load_problem, InputValidator, Problem
from .agents import (LLMResult, OfflineResponse, ReasoningAgent, TranslationAgent,
                     SemanticReviewerAgent,
                     TranslationRepairContext)
from .parser import (parse_reasoning_action, parse_translator_output,
                     parse_semantic_review)
from .structure import (check_filled_placeholder, check_structure,
                        body_contains_sorry, declared_names,
                        exploration_progress_violations,
                        exploration_sorry_violations)
from .verifier_client import make_verifier, build_compile_json
from .reconstructor import RenderedSource, SourceRegion, render_source
from .artifacts import Paths, LeanArtifactLogger, RunEventLogger, StateManager

CONSID = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "considerations")


def _read(p): return open(p).read()
def _now(): return time.time()


def _account_llm_call(state: ProofRunState, role: str,
                      result: Optional[LLMResult] = None):
    """Record one attempted call without inventing missing provider usage."""
    if role not in ("reasoner", "translator", "reviewer"):
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


@dataclass
class _ResumeCursor:
    step: int
    candidate: int
    attempt: Optional[int]
    kind: str                         # reasoner | translator | reviewer
    saved_prompt: str = ""
    archive_root: str = ""


def _load_saved_problem(root: str, state: ProofRunState) -> Problem:
    """Load the immutable problem snapshot stored with a run."""
    input_root = os.path.join(root, "input")
    config = Config(**json.load(open(os.path.join(root, "config.json"))))
    informal_path = os.path.join(input_root, "informal_problem.md")
    context_path = os.path.join(input_root, "context.lean")
    body_path = os.path.join(input_root, "body_initial.lean")
    placeholder_path = os.path.join(input_root, "placeholder.lean")
    placeholder = (
        open(placeholder_path).read() if os.path.isfile(placeholder_path) else None
    )
    return Problem(
        problem_id=state.problem_id,
        root=root,
        config=config,
        informal_problem=open(informal_path).read(),
        context=open(context_path).read(),
        body_initial=open(body_path).read(),
        context_path=context_path,
        initial_body_path=body_path,
        informal_problem_path=informal_path,
        placeholder=placeholder,
        placeholder_path=placeholder_path if placeholder is not None else None,
    )


def _find_resume_cursor(root: str, state: ProofRunState) -> _ResumeCursor:
    if state.status == "final_success":
        raise ValueError("cannot resume a successful run")
    terminal = next(
        (step for step in reversed(state.proof_steps)
         if step.status not in ("accepted", "final_success")),
        None,
    )
    if terminal is None:
        raise ValueError("run has no failed or incomplete proof step to resume")

    for candidate in terminal.informal_candidates:
        # A reasoner failure has no usable informal action or Lean attempts.
        if candidate.reasoning_call_error_path or (
            candidate.status == "pending" and not candidate.lean_translation_attempts
            and (not candidate.informal_step_path
                 or not os.path.exists(candidate.informal_step_path)
                 or os.path.getsize(candidate.informal_step_path) == 0)
        ):
            return _ResumeCursor(
                terminal.proof_step_index,
                candidate.informal_candidate_index,
                None,
                "reasoner",
            )
        for attempt in candidate.lean_translation_attempts:
            pending_review = next(
                (review for review in attempt.semantic_review_attempts
                 if review.status == "pending"),
                None,
            )
            if pending_review is not None:
                prompt = ""
                if pending_review.prompt_path and os.path.exists(pending_review.prompt_path):
                    prompt = open(pending_review.prompt_path).read()
                return _ResumeCursor(
                    terminal.proof_step_index,
                    candidate.informal_candidate_index,
                    attempt.lean_translation_attempt_index,
                    "reviewer",
                    saved_prompt=prompt,
                )
            if attempt.status in ("translation_call_failed", "pending"):
                prompt = ""
                if attempt.translator_prompt_path and os.path.exists(
                    attempt.translator_prompt_path
                ):
                    prompt = open(attempt.translator_prompt_path).read()
                return _ResumeCursor(
                    terminal.proof_step_index,
                    candidate.informal_candidate_index,
                    attempt.lean_translation_attempt_index,
                    "translator",
                    saved_prompt=prompt,
                )
    raise ValueError(
        "terminal proof step contains no interrupted reasoner, translator, or reviewer call"
    )


def _validate_resume_prefix(paths: Paths, state: ProofRunState,
                            cursor: _ResumeCursor):
    """Fail before archival if the authoritative prefix cannot be rebuilt."""
    for step in state.proof_steps:
        if step.proof_step_index >= cursor.step:
            break
        if step.status not in ("accepted", "final_success"):
            raise ValueError(
                "resume state has a non-accepted step before the selected checkpoint"
            )
        accepted_dir = paths.accepted_ps(step.proof_step_index)
        for filename in ("declarations.lean", "body.lean"):
            if not os.path.isfile(os.path.join(accepted_dir, filename)):
                raise ValueError(
                    f"accepted artifacts missing for proof step "
                    f"{step.proof_step_index}"
                )
    if cursor.kind in ("translator", "reviewer"):
        step = next(s for s in state.proof_steps
                    if s.proof_step_index == cursor.step)
        candidate = next(c for c in step.informal_candidates
                         if c.informal_candidate_index == cursor.candidate)
        if (not candidate.informal_step_path
                or not os.path.isfile(candidate.informal_step_path)
                or os.path.getsize(candidate.informal_step_path) == 0):
            raise ValueError("saved informal candidate is missing")


def _archive_and_rewind(paths: Paths, state: ProofRunState,
                        cursor: _ResumeCursor) -> _ResumeCursor:
    """Archive the superseded suffix and trim state to the resume boundary."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    archive_root = os.path.join(paths.root, "archive", f"resume_{stamp}")
    os.makedirs(archive_root, exist_ok=False)
    shutil.copy2(os.path.join(paths.root, "state.json"),
                 os.path.join(archive_root, "state.json"))

    def archive(path: str):
        if not os.path.exists(path):
            return
        relative = os.path.relpath(path, paths.root)
        destination = os.path.join(archive_root, relative)
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.move(path, destination)

    step_pos = next(
        n for n, step in enumerate(state.proof_steps)
        if step.proof_step_index == cursor.step
    )
    step = state.proof_steps[step_pos]
    candidate_pos = next(
        n for n, candidate in enumerate(step.informal_candidates)
        if candidate.informal_candidate_index == cursor.candidate
    )

    if cursor.kind == "translator":
        candidate = step.informal_candidates[candidate_pos]
        assert cursor.attempt is not None
        for attempt in candidate.lean_translation_attempts:
            if attempt.lean_translation_attempt_index >= cursor.attempt:
                archive(paths.la(cursor.step, cursor.candidate,
                                 attempt.lean_translation_attempt_index))
        candidate.lean_translation_attempts = [
            attempt for attempt in candidate.lean_translation_attempts
            if attempt.lean_translation_attempt_index < cursor.attempt
        ]
        candidate.status = "pending"
        candidates_to_archive = step.informal_candidates[candidate_pos + 1:]
        step.informal_candidates = step.informal_candidates[:candidate_pos + 1]
    elif cursor.kind == "reviewer":
        candidate = step.informal_candidates[candidate_pos]
        assert cursor.attempt is not None
        selected = next(
            attempt for attempt in candidate.lean_translation_attempts
            if attempt.lean_translation_attempt_index == cursor.attempt
        )
        archive(os.path.join(
            paths.la(cursor.step, cursor.candidate, cursor.attempt),
            "semantic_reviews",
        ))
        selected.semantic_review_attempts = []
        for attempt in candidate.lean_translation_attempts:
            if attempt.lean_translation_attempt_index > cursor.attempt:
                archive(paths.la(cursor.step, cursor.candidate,
                                 attempt.lean_translation_attempt_index))
        candidate.lean_translation_attempts = [
            attempt for attempt in candidate.lean_translation_attempts
            if attempt.lean_translation_attempt_index <= cursor.attempt
        ]
        candidate.status = "pending"
        candidates_to_archive = step.informal_candidates[candidate_pos + 1:]
        step.informal_candidates = step.informal_candidates[:candidate_pos + 1]
    else:
        candidates_to_archive = step.informal_candidates[candidate_pos:]
        step.informal_candidates = step.informal_candidates[:candidate_pos]

    for candidate in candidates_to_archive:
        archive(paths.ic(cursor.step, candidate.informal_candidate_index))

    for later_step in state.proof_steps[step_pos + 1:]:
        archive(paths.ps(later_step.proof_step_index))
        archive(paths.accepted_ps(later_step.proof_step_index))
    state.proof_steps = state.proof_steps[:step_pos + 1]
    step.status = "pending"

    archive(paths.failure)
    archive(paths.final)
    archive(paths.tmp)
    state.status = "running"
    state.failure_reason = None
    cursor.archive_root = archive_root
    with open(os.path.join(archive_root, "resume.json"), "w") as f:
        json.dump({
            "step": cursor.step,
            "candidate": cursor.candidate,
            "attempt": cursor.attempt,
            "kind": cursor.kind,
        }, f, indent=2)
        f.write("\n")
    return cursor


def _resume_repair_context(candidate: InformalCandidate):
    """Rehydrate the immediately preceding translator repair transaction."""
    if not candidate.lean_translation_attempts:
        return "", None
    previous = candidate.lean_translation_attempts[-1]
    if previous.status in ("translation_call_failed", "parse_error"):
        return "", None
    if not previous.compile_path or not os.path.isfile(previous.compile_path):
        return "", None
    compile_json = CompileJson.model_validate_json(open(previous.compile_path).read())
    if previous.status in (
        "semantic_misalignment", "reviewer_call_failed", "reviewer_parse_error"
    ):
        feedback = (compile_json.semantic_review.feedback or previous.status)
        declarations = (
            open(previous.declarations_path).read()
            if previous.declarations_path and os.path.isfile(previous.declarations_path)
            else ""
        )
        body = (
            open(previous.body_path).read()
            if previous.body_path and os.path.isfile(previous.body_path) else ""
        )
        placeholder = (
            open(previous.placeholder_path).read()
            if previous.placeholder_path and os.path.isfile(previous.placeholder_path)
            else ""
        )
        raw = (
            open(previous.raw_translator_output_path).read()
            if previous.raw_translator_output_path
            and os.path.isfile(previous.raw_translator_output_path)
            else ""
        )
        return "Semantic alignment review failed:\n" + feedback, TranslationRepairContext(
            failed_check="semantic_alignment",
            raw_output=raw,
            declarations=declarations,
            placeholder=placeholder,
            body=body,
            diagnostics=feedback,
            attempt_kind=previous.attempt_kind,
        )
    report_name = {
        "lemma_failed": "declaration_check",
        "body_failed": "body_check",
        "placeholder_fill_failed": "body_check",
        "final_reconstruction_failed": "final_check",
        "hard_full_reconstruction_failed": "final_check",
    }.get(previous.status, "structure_check")
    report = getattr(compile_json, report_name)
    diagnostic_lines = []
    for error in report.errors:
        diagnostic_lines.append(
            f"{error.file}:{error.line}:{error.col}: {error.severity}: "
            f"{error.message}\nSource region: **{error.source_region or 'unknown'}**"
            + (f"\nNearby submitted Lean code:\n{error.nearby_code}"
               if error.nearby_code else "")
        )
    diagnostics = "\n\n".join(diagnostic_lines) or previous.status
    declarations = (
        open(previous.declarations_path).read()
        if previous.declarations_path and os.path.isfile(previous.declarations_path)
        else ""
    )
    body = (
        open(previous.body_path).read()
        if previous.body_path and os.path.isfile(previous.body_path) else ""
    )
    placeholder = (
        open(previous.placeholder_path).read()
        if previous.placeholder_path and os.path.isfile(previous.placeholder_path)
        else ""
    )
    raw = (
        open(previous.raw_translator_output_path).read()
        if previous.raw_translator_output_path
        and os.path.isfile(previous.raw_translator_output_path)
        else ""
    )
    feedback = {
        "declaration_check": "Declaration failed to compile:\n",
        "body_check": "Body failed to compile:\n",
        "final_check": "Reconstructed solution failed to compile:\n",
        "structure_check": "Structure check failed:\n",
    }[report_name] + diagnostics
    return feedback, TranslationRepairContext(
        failed_check=report_name,
        raw_output=raw,
        declarations=declarations,
        placeholder=placeholder,
        body=body,
        diagnostics=diagnostics,
        attempt_kind=previous.attempt_kind,
    )


def run_problem(problem_dir: str, runs_root: Optional[str] = None,
                reasoning_offline: Optional[List[OfflineResponse]] = None,
                translation_offline: Optional[List[OfflineResponse]] = None,
                reviewer_offline: Optional[List[OfflineResponse]] = None,
                *, _resume_root: Optional[str] = None) -> ProofRunState:
    resume_cursor = None
    if _resume_root is not None:
        resume_root = os.path.abspath(_resume_root)
        paths = Paths(os.path.dirname(resume_root), os.path.basename(resume_root))
        statemgr = StateManager(paths)
        state = statemgr.load(paths.root)
        resume_cursor = _find_resume_cursor(paths.root, state)
        _validate_resume_prefix(paths, state, resume_cursor)
        prob = _load_saved_problem(paths.root, state)
        problem_dir = paths.root
        problem_id = state.problem_id
        events = RunEventLogger(paths, resume=True)
        events.event("-" * 72, elapsed_ms=0)
        events.event(f"Resume started: {problem_id}", console=True, elapsed_ms=0)
    else:
        problem_dir = os.path.abspath(problem_dir)
        problem_id = os.path.basename(problem_dir.rstrip("/"))
        runs_root = runs_root or os.path.join(problem_dir, "runs")
        paths = Paths(runs_root, problem_id)
        statemgr = StateManager(paths)
        events = RunEventLogger(paths)
        events.event(f"Run started: {problem_id}", console=True, elapsed_ms=0)
        try:
            prob = load_problem(problem_dir)
        except Exception as error:
            events.event(
                f"Problem loading failed: {type(error).__name__}: {error}",
                indent=1, console=True,
            )
            events.close()
            raise
    events.event(f"Problem snapshot: {problem_dir}", indent=1)
    events.event(f"Run directory: {paths.root}", indent=1)
    events.event(
        f"Configuration: mode={prob.config.problem_mode}, "
        f"reasoner={prob.config.reasoning_model}, "
        f"translator={prob.config.translation_model}, "
        f"reviewer={prob.config.reviewer_model or prob.config.translation_model}, "
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

    if resume_cursor is not None:
        resume_cursor = _archive_and_rewind(paths, state, resume_cursor)
        logger = LeanArtifactLogger(paths)
        events.event(
            f"Resume checkpoint: proof_step_{resume_cursor.step:03d}/"
            f"informal_candidate_{resume_cursor.candidate:03d}/"
            + (f"lean4_attempt_{resume_cursor.attempt:03d}"
               if resume_cursor.attempt is not None else "reasoner_call"),
            indent=1, console=True,
        )
        events.event(f"Superseded suffix archived at {resume_cursor.archive_root}",
                     indent=1)
    else:
        logger = LeanArtifactLogger(paths)
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
    previous_runtime = state.stats.runtime_seconds if resume_cursor is not None else 0.0
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
        state.stats.runtime_seconds = previous_runtime + (_now() - start)
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
    translation = TranslationAgent(
        prob.config.translation_model,
        _read(os.path.join(CONSID, "FORMAL_TRANSLATION_CONSIDERATIONS.md")),
        translation_offline,
        reasoning_effort=prob.config.translator_reasoning_effort,
    )
    reviewer = SemanticReviewerAgent(
        prob.config.reviewer_model or prob.config.translation_model,
        _read(os.path.join(CONSID, "SEMANTIC_REVIEW_CONSIDERATIONS.md")),
        reviewer_offline,
        reasoning_effort=(
            prob.config.reviewer_reasoning_effort
            if prob.config.reviewer_reasoning_effort is not None
            else prob.config.translator_reasoning_effort
        ),
    )

    def semantic_review(i, j, k, la, action, informal_candidate,
                        attempt_kind, translator_result, parsed,
                        accepted_declarations, current_body,
                        saved_prompt=""):
        """Review one compiling transaction, retrying only reviewer infrastructure."""
        current_file = render_source(
            prelude, prob.context, accepted_declarations,
            placeholder=(placeholder_initial_source or "")
            if problem_mode == "hard" else "",
            theorem_body=current_body,
        ).text
        candidate_placeholder = (
            placeholder_initial_source or ""
            if attempt_kind == "exploration" and problem_mode == "hard"
            else parsed.placeholder or ""
            if attempt_kind == "hard_finalization"
            else ""
        )
        candidate_file = render_source(
            prelude, prob.context, accepted_declarations,
            candidate_declarations=parsed.declarations,
            placeholder=candidate_placeholder,
            theorem_body=parsed.body,
        ).text
        prompt = saved_prompt or reviewer.build_prompt(
            prob.informal_problem,
            informal_candidate,
            current_file,
            translator_result.text,
            candidate_file,
            attempt_kind=attempt_kind,
            answer=action.answer,
        )
        last_status = "reviewer_call_failed"
        last_feedback = "Semantic reviewer call did not return."
        for review_index in range(1, prob.config.max_reviewer_call_attempts + 1):
            review_state = SemanticReviewAttempt(
                semantic_review_attempt_index=review_index,
            )
            la.semantic_review_attempts.append(review_state)
            review_state.prompt_path = logger.write_reviewer_prompt(
                i, j, k, review_index, prompt
            )
            events.event(
                f"Semantic reviewer attempt {review_index} started: "
                f"{review_state.prompt_path} ({len(prompt)} chars)",
                indent=4,
            )
            statemgr.save(state)
            if _now() >= deadline:
                review_state.status = "call_failed"
                last_feedback = "Semantic reviewer was not called because the runtime limit was reached."
                review_state.call_error_path = logger.write_reviewer_error(
                    i, j, k, review_index, last_feedback
                )
                return False, SemanticReviewReport(
                    status="call_failed", feedback=last_feedback
                ), "reviewer_call_failed", last_feedback
            timeout = call_timeout()
            events.event(
                f"Waiting for semantic reviewer response (model={reviewer.model}, "
                f"timeout={timeout:.1f}s)",
                indent=4, console=True,
            )
            started = _now()
            try:
                result = reviewer.review(
                    timeout=timeout, prepared_prompt=prompt
                )
            except Exception as error:
                _account_llm_call(state, "reviewer")
                review_state.status = "call_failed"
                last_status = "reviewer_call_failed"
                last_feedback = (
                    f"semantic reviewer call failed: {type(error).__name__}: "
                    f"{str(error)[:500]}"
                )
                review_state.call_error_path = logger.write_reviewer_error(
                    i, j, k, review_index, last_feedback
                )
                events.event(
                    f"Semantic reviewer call failed after {_now() - started:.3f}s: "
                    f"{type(error).__name__}: {str(error)[:200]}",
                    indent=4, console=True,
                )
                statemgr.save(state)
                continue
            _account_llm_call(state, "reviewer", result)
            review_state.raw_output_path = logger.write_reviewer_output(
                i, j, k, review_index, result.text
            )
            events.event(
                f"Semantic reviewer response received after {_now() - started:.3f}s "
                f"({len(result.text)} chars)", indent=4, console=True,
            )
            events.event(_usage_event("reviewer", result), indent=5)
            parsed_review = parse_semantic_review(result.text)
            if not parsed_review.ok:
                review_state.status = "parse_error"
                last_status = "reviewer_parse_error"
                last_feedback = f"Semantic reviewer output was invalid: {parsed_review.error}"
                review_state.feedback = last_feedback
                events.event(
                    f"Semantic reviewer output parse failed: {parsed_review.error}",
                    indent=4, console=True,
                )
                statemgr.save(state)
                continue
            review_state.verdict = parsed_review.verdict
            review_state.feedback = parsed_review.feedback
            if parsed_review.verdict == "ALIGNED":
                review_state.status = "passed"
                statemgr.save(state)
                events.event("Semantic alignment review passed", indent=4, console=True)
                return True, SemanticReviewReport(
                    status="passed", verdict="ALIGNED",
                    feedback=parsed_review.feedback,
                ), "", ""
            review_state.status = "failed"
            statemgr.save(state)
            events.event("Semantic alignment review rejected the transaction",
                         indent=4, console=True)
            return False, SemanticReviewReport(
                status="failed", verdict="MISALIGNED",
                feedback=parsed_review.feedback,
            ), "semantic_misalignment", parsed_review.feedback
        return False, SemanticReviewReport(
            status="call_failed" if last_status == "reviewer_call_failed" else "parse_error",
            feedback=last_feedback,
        ), last_status, last_feedback

    accepted_decls: List[str] = []
    latest_body = prob.body_initial
    if resume_cursor is not None:
        for accepted_step in state.proof_steps:
            if accepted_step.proof_step_index >= resume_cursor.step:
                break
            if accepted_step.status not in ("accepted", "final_success"):
                raise ValueError(
                    "resume state has a non-accepted step before the selected checkpoint"
                )
            accepted_dir = paths.accepted_ps(accepted_step.proof_step_index)
            declarations_path = os.path.join(accepted_dir, "declarations.lean")
            body_path = os.path.join(accepted_dir, "body.lean")
            if not os.path.isfile(declarations_path) or not os.path.isfile(body_path):
                raise ValueError(
                    f"accepted artifacts missing for proof step "
                    f"{accepted_step.proof_step_index}"
                )
            accepted_decls.append(open(declarations_path).read())
            latest_body = open(body_path).read()
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

        active_resume = resume_cursor
        resume_cursor = None
        if active_resume is not None:
            i = active_resume.step
            ps = state.proof_steps[-1]
            events.event(f"Proof step {i} resumed", indent=1, console=True)
        else:
            i = len(state.proof_steps) + 1
            ps = ProofStep(proof_step_index=i)
            state.proof_steps.append(ps)
            events.event(f"Proof step {i} started", indent=1, console=True)
        step_accepted = False
        reasoning_feedback = ""
        failed_next_step = ""

        candidate_start = active_resume.candidate if active_resume is not None else 1
        for j in range(candidate_start,
                       prob.config.max_informal_candidates_per_proof_step + 1):
            resuming_transaction = (
                active_resume is not None
                and active_resume.kind in ("translator", "reviewer")
                and j == active_resume.candidate
            )
            resuming_reviewer = (
                resuming_transaction and active_resume.kind == "reviewer"
            )
            if resuming_transaction:
                ic = ps.informal_candidates[-1]
                events.event(f"Candidate {j} resumed", indent=2, console=True)
            else:
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
            if resuming_transaction:
                if not ic.informal_step_path or not os.path.isfile(
                    ic.informal_step_path
                ):
                    raise ValueError("saved informal candidate is missing")
                r = LLMResult(
                    prompt="",
                    text=open(ic.informal_step_path).read(),
                    model=reasoning.model + "[saved]",
                )
            else:
                reasoning_prompt = reasoning.build_prompt(
                    prob.informal_problem,
                    state.current_knowledge,
                    problem_mode=prob.config.problem_mode,
                    failure_feedback=reasoning_feedback,
                    failed_next_step=failed_next_step,
                    future_ideas=state.future_ideas,
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
                except Exception as e:                      # timeout / API error
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
                ic.reasoning_call_error_path = None
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
            action = parse_reasoning_action(
                r.text,
                prob.config.problem_mode,
                allow_legacy_step_usefulness=resuming_transaction,
            )

            if not action.ok:
                last_error = f"invalid reasoner action: {action.error}"
                bump("invalid_reasoner_action")
                ic.status = "abandoned"
                reasoning_feedback = (
                    "The previous response was not a valid action: "
                    f"{action.error}. Return one action with non-empty NEXT STEP and PROOF, "
                    "an exact IS_FINAL_STEP Boolean, and non-empty IDEAS FOR THE "
                    "FUTURE as the final section."
                )
                failed_next_step = ""
                events.event(
                    f"Reasoner action rejected: {action.error}",
                    indent=3, console=True,
                )
                statemgr.save(state)
                continue

            ic.future_ideas = action.future_ideas
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
            candidate_rejection_feedback = ""
            candidate_semantic_feedback = ""
            if resuming_transaction:
                compiler_feedback, repair_context = _resume_repair_context(ic)
                attempt_start = active_resume.attempt
            else:
                compiler_feedback = ""
                repair_context = None
                attempt_start = 1
            for k in range(attempt_start,
                           prob.config.max_lean_translation_attempts_per_candidate + 1):
                lim = limits.exceeded(state)
                if lim:
                    events.event(f"Run limit reached: {lim}", indent=3, console=True)
                    failures.write(state, lim, _env_text(
                                       prelude, prob.context, accepted_decls,
                                       placeholder_initial_source
                                       if problem_mode == "hard" else ""),
                                   latest_body, last_error, category_counts)
                    return finish()

                reviewer_resume_now = resuming_reviewer and k == attempt_start
                if reviewer_resume_now:
                    la = next(
                        attempt for attempt in ic.lean_translation_attempts
                        if attempt.lean_translation_attempt_index == k
                    )
                    if (not la.raw_translator_output_path
                            or not os.path.isfile(la.raw_translator_output_path)):
                        raise ValueError("saved reviewer checkpoint lacks translator output")
                    t = LLMResult(
                        prompt=(open(la.translator_prompt_path).read()
                                if la.translator_prompt_path
                                and os.path.isfile(la.translator_prompt_path) else ""),
                        text=open(la.raw_translator_output_path).read(),
                        model=translation.model + "[saved]",
                    )
                    events.event(
                        f"Lean translation attempt {k} resumed at semantic review",
                        indent=3, console=True,
                    )
                else:
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
                    if (resuming_transaction and active_resume.kind == "translator"
                            and k == attempt_start and active_resume.saved_prompt):
                        translation_prompt = active_resume.saved_prompt
                    else:
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
                    except Exception as e:                  # timeout / API error
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
                        "Translator output parse failed; retrying",
                        indent=4, console=True,
                    )
                    statemgr.save(state); continue

                if pr.rejected:
                    la.status = "translator_rejected_step"
                    la.translator_rejection_kind = pr.rejection_kind
                    la.translator_rejection_reason = pr.rejection_reason
                    cj = CompileJson(
                        attempt_status="translator_rejected_step",
                        attempt_kind=attempt_kind,
                        translator_rejection_kind=pr.rejection_kind,
                        translator_rejection_reason=pr.rejection_reason,
                    )
                    _log_attempt(
                        logger, la, paths, i, j, k, t, None, None, cj
                    )
                    bump("translator_rejected_step")
                    last_error = (
                        f"translator rejected step ({pr.rejection_kind}): "
                        f"{pr.rejection_reason}"
                    )
                    candidate_rejection_feedback = (
                        f"The translator rejected this step as {pr.rejection_kind}: "
                        f"{pr.rejection_reason}"
                    )
                    events.event(
                        f"Translator rejected candidate as {pr.rejection_kind}; "
                        "skipping remaining translation attempts",
                        indent=4, console=True,
                    )
                    statemgr.save(state)
                    break

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
                sorry_violations = []
                progress_violations = []
                if attempt_kind == "exploration":
                    sorry_violations = exploration_sorry_violations(
                        latest_body, pr.body
                    )
                    progress_violations = exploration_progress_violations(
                        latest_body, pr.declarations, pr.body
                    )
                    sr.violations.extend(sorry_violations + progress_violations)
                    sr.ok = sr.ok and not sorry_violations and not progress_violations
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
                        else "unproved_body_fact"
                        if attempt_kind == "exploration" and sorry_violations
                        else "no_formal_progress"
                        if attempt_kind == "exploration" and progress_violations
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
                    elif status == "unproved_body_fact":
                        bump("unproved_body_fact")
                    elif status == "no_formal_progress":
                        bump("no_formal_progress")
                    else:
                        bump("changed_theorem_statement"
                             if any("header" in v for v in sr.violations)
                             else "bad_output_format")
                    structure_feedback = (
                        "Structure check failed:\n"
                        + "\n".join(f"- {v}" for v in sr.violations)
                    )
                    if status == "unproved_body_fact":
                        last_error = structure_feedback
                        candidate_rejection_feedback = (
                            "The translator tried to justify a new fact with `sorry` "
                            "inside the theorem body. The candidate was rejected before "
                            "compilation; propose a smaller or different step."
                        )
                        events.event(
                            "Unproved theorem-body fact detected; skipping compilation "
                            "and remaining translation attempts",
                            indent=4, console=True,
                        )
                        for violation in sr.violations:
                            events.event(violation, indent=5)
                        statemgr.save(state)
                        break
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

                aligned, review_report, review_status, review_feedback = semantic_review(
                    i, j, k, la, action, informal_candidate, attempt_kind,
                    t, pr, accepted_decls, latest_body,
                    saved_prompt=(active_resume.saved_prompt
                                  if reviewer_resume_now else ""),
                )
                if not aligned:
                    status = review_status
                    cj = build_compile_json(
                        status, sr, cp, attempt_kind=attempt_kind
                    )
                    cj.semantic_review = review_report
                    _log_attempt(
                        logger, la, paths, i, j, k, t,
                        pr.declarations, pr.body, cj,
                        placeholder=pr.placeholder,
                    )
                    la.status = status
                    bump(status)
                    last_error = review_feedback
                    candidate_semantic_feedback = (
                        "The semantic reviewer rejected the compiling translation: "
                        + review_feedback
                    )
                    compiler_feedback = "Semantic alignment review failed:\n" + review_feedback
                    repair_context = TranslationRepairContext(
                        failed_check="semantic_alignment",
                        raw_output=t.text,
                        declarations=pr.declarations,
                        placeholder=pr.placeholder or "",
                        body=pr.body,
                        diagnostics=review_feedback,
                        attempt_kind=attempt_kind,
                    )
                    events.event(
                        f"Compiling transaction rejected by semantic review ({status}); retrying",
                        indent=4, console=True,
                    )
                    statemgr.save(state)
                    continue

                # ---- both checks passed ----
                if attempt_kind == "exploration":
                    cj = build_compile_json(
                        "accepted", sr, cp, attempt_kind=attempt_kind
                    )
                    cj.semantic_review = review_report
                    _log_attempt(
                        logger, la, paths, i, j, k, t,
                        pr.declarations, pr.body, cj,
                    )
                    la.status = "accepted"; ic.status = "accepted"; ps.status = "accepted"
                    logger.copy_accepted(i, pr.declarations, pr.body)
                    accepted_decls.append(pr.declarations); latest_body = pr.body
                    state.stats.accepted_proof_steps += 1
                    state.current_knowledge.append(AcceptedKnowledge(
                        statement=action.next_step,
                    ))
                    state.future_ideas = action.future_ideas
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
                        cj.semantic_review = review_report
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
                        state.future_ideas = action.future_ideas
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
                        cj.semantic_review = review_report
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
                (f"Candidate {j} abandoned after translator rejection"
                 if candidate_rejection_feedback else
                 f"Candidate {j} abandoned after exhausting translation attempts"),
                indent=2, console=True,
            )
            failed_next_step = action.next_step
            reasoning_feedback = (candidate_rejection_feedback
                or candidate_semantic_feedback) or (
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


def resume_problem(run_root: str,
                   reasoning_offline: Optional[List[OfflineResponse]] = None,
                   translation_offline: Optional[List[OfflineResponse]] = None,
                   reviewer_offline: Optional[List[OfflineResponse]] = None,
                   ) -> ProofRunState:
    """Resume the earliest interrupted LLM call in a run's terminal step."""
    return run_problem(
        "",
        reasoning_offline=reasoning_offline,
        translation_offline=translation_offline,
        reviewer_offline=reviewer_offline,
        _resume_root=run_root,
    )


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
