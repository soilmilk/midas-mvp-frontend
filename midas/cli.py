#!/usr/bin/env python3
"""
midas-mvp CLI (SPEC Phase 5). Usable without reading source:

  python3 -m midas.cli run <problem>          # id or dir: p4_n5_30  OR  problems/p4_n5_30
  python3 -m midas.cli resume <problem_id>    # continue an interrupted or runtime-limited run
  python3 -m midas.cli custom-resume <source> <destination> --step N --candidate N --stage STAGE
  python3 -m midas.cli status <problem_id>
  python3 -m midas.cli attempts <problem_id> [--step N] [--failed-only]
  python3 -m midas.cli show <problem_id> <step> <candidate> <attempt>
  python3 -m midas.cli replay <problem_id> <step> <candidate> <attempt>

`run` needs OPENROUTER_API_KEY (source ~/.midas-mvp.env). `replay` recompiles a single
checkpoint via the verifier only — no LLM call — for debugging without burning API calls.
Runs live under ./runs/<problem_id>/ (override with --runs-root).
"""
from __future__ import annotations
import argparse, json, os, re, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from midas.artifacts import StateManager, Paths
from midas.models import Config, ProofRunState


def _pid(arg):
    """Normalize a problem argument to its id, so an id OR a path both work
    (`p4_n5_30` and `problems/p4_n5_30` are equivalent everywhere)."""
    return os.path.basename(arg.rstrip("/"))


def _run_root(args, problem):
    return os.path.join(os.path.abspath(args.runs_root), _pid(problem))


def _resolve_problem_dir(arg):
    """For `run`: accept an id or a path. Look for <arg>/ then problems/<arg>/."""
    for c in (arg, os.path.join("problems", _pid(arg))):
        if os.path.isdir(c) and os.path.exists(os.path.join(c, "config.json")):
            return os.path.abspath(c)
    sys.exit(f"problem not found: {arg!r} — looked for '{arg}/config.json' and "
             f"'problems/{_pid(arg)}/config.json' (run from the repo root)")


def _load_state(root) -> ProofRunState:
    if not os.path.exists(os.path.join(root, "state.json")):
        sys.exit(f"no run found at {root} (run it first)")
    return StateManager.load(root)


def _usage_summary(usage):
    return (
        f"tokens={usage.total_tokens} "
        f"cost_credits={usage.cost_credits:.10g} "
        f"token_usage_calls={usage.calls_with_token_usage}/{usage.calls} "
        f"cost_calls={usage.calls_with_cost}/{usage.calls}"
    )


def cmd_run(args):
    from midas.loop import run_problem
    from midas.artifacts import RunDirectoryExistsError
    problem_dir = _resolve_problem_dir(args.problem_dir)
    run_root = _run_root(args, problem_dir)
    if os.path.exists(run_root):
        sys.exit(str(RunDirectoryExistsError(run_root)))
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("warning: OPENROUTER_API_KEY not set — live agents will fail. `source ~/.midas-mvp.env`",
              file=sys.stderr)
    try:
        state = run_problem(
            problem_dir,
            runs_root=os.path.abspath(args.runs_root),
        )
    except RunDirectoryExistsError as error:
        sys.exit(str(error))
    print(f"{state.problem_id}: {state.status}"
          + (f" ({state.failure_reason})" if state.failure_reason else "")
          + f"  | accepted={state.stats.accepted_proof_steps} llm={state.stats.total_llm_calls} "
            f"compiles={state.stats.total_lean_compiles} attempts={state.stats.total_lean_attempts} "
            f"runtime={state.stats.runtime_seconds:.0f}s "
            + _usage_summary(state.stats.llm_usage.total))


def cmd_resume(args):
    from midas.loop import resume_problem
    root = _run_root(args, args.problem_id)
    if not os.path.exists(os.path.join(root, "state.json")):
        sys.exit(f"no run found at {root} (run it first)")
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("warning: OPENROUTER_API_KEY not set — live agents will fail. `source ~/.midas-mvp.env`",
              file=sys.stderr)
    try:
        state = resume_problem(root)
    except ValueError as error:
        sys.exit(f"cannot resume {args.problem_id}: {error}")
    print(f"{state.problem_id}: {state.status}"
          + (f" ({state.failure_reason})" if state.failure_reason else "")
          + f"  | accepted={state.stats.accepted_proof_steps} llm={state.stats.total_llm_calls} "
            f"compiles={state.stats.total_lean_compiles} attempts={state.stats.total_lean_attempts} "
            f"runtime={state.stats.runtime_seconds:.0f}s "
            + _usage_summary(state.stats.llm_usage.total))


def cmd_custom_resume(args):
    from midas.loop import custom_resume_problem
    from midas.artifacts import RunDirectoryExistsError
    source = _run_root(args, args.source_run_id)
    destination = _run_root(args, args.new_run_id)
    if not os.path.exists(os.path.join(source, "state.json")):
        sys.exit(f"no run found at {source}")
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("warning: OPENROUTER_API_KEY not set — live agents will fail. `source ~/.midas-mvp.env`",
              file=sys.stderr)
    try:
        state = custom_resume_problem(
            source,
            destination,
            step=args.step,
            candidate=args.candidate,
            stage=args.stage,
            attempt=args.attempt,
            reviewer_attempt=args.reviewer_attempt,
        )
    except (ValueError, RunDirectoryExistsError) as error:
        sys.exit(f"cannot custom-resume {args.source_run_id}: {error}")
    print(f"{args.new_run_id}: {state.status}"
          + (f" ({state.failure_reason})" if state.failure_reason else "")
          + f"  | source={args.source_run_id} accepted={state.stats.accepted_proof_steps} "
            f"llm={state.stats.total_llm_calls} compiles={state.stats.total_lean_compiles} "
            f"attempts={state.stats.total_lean_attempts} "
            f"runtime={state.stats.runtime_seconds:.0f}s "
            + _usage_summary(state.stats.llm_usage.total))


def cmd_status(args):
    root = _run_root(args, args.problem_id)
    st = _load_state(root)
    print(f"problem : {st.problem_id}")
    print(f"mode    : {st.problem_mode}")
    print(f"status  : {st.status}" + (f"  ({st.failure_reason})" if st.failure_reason else ""))
    print(f"header  : {st.formal_theorem_header!r}")
    branch_path = os.path.join(root, "branch.json")
    if os.path.isfile(branch_path):
        branch = json.load(open(branch_path))
        checkpoint = branch.get("checkpoint", {})
        print(f"branch  : {branch.get('source_run', '')}")
        print(
            "checkpoint: "
            f"step={checkpoint.get('step')} candidate={checkpoint.get('candidate')} "
            f"stage={checkpoint.get('stage')} attempt={checkpoint.get('attempt')} "
            f"reviewer_attempt={checkpoint.get('reviewer_attempt')}"
        )
    if st.problem_mode == "hard":
        print(f"placeholder status : {st.placeholder_status or 'unknown'}")
        print(f"placeholder name   : {st.placeholder_name or '(unknown)'}")
        print(f"placeholder header : {st.placeholder_header!r}")
    s = st.stats
    print(f"stats   : accepted_steps={s.accepted_proof_steps} llm_calls={s.total_llm_calls} "
          f"lean_compiles={s.total_lean_compiles} lean_attempts={s.total_lean_attempts} "
          f"runtime={s.runtime_seconds:.0f}s")
    print(f"usage   : total      {_usage_summary(s.llm_usage.total)}")
    print(f"          reasoner   {_usage_summary(s.llm_usage.reasoner)}")
    print(f"          translator {_usage_summary(s.llm_usage.translator)}")
    print("steps   :")
    for ps in st.proof_steps:
        n_cand = len(ps.informal_candidates)
        n_att = sum(len(c.lean_translation_attempts) for c in ps.informal_candidates)
        print(f"  proof_step_{ps.proof_step_index:03d}  {ps.status:<14} candidates={n_cand} attempts={n_att}")


def cmd_attempts(args):
    root = _run_root(args, args.problem_id)
    st = _load_state(root)
    p = Paths(os.path.dirname(root), os.path.basename(root))
    print(
        f"{'step':>4} {'cand':>4} {'att':>4}  {'kind':<18} "
        f"{'status':<34} {'review':<11} {'declarations?':<13} placeholder?"
    )
    print("-" * 112)
    for ps in st.proof_steps:
        if args.step and ps.proof_step_index != args.step:
            continue
        for ic in ps.informal_candidates:
            for la in ic.lean_translation_attempts:
                if args.failed_only and la.status in ("accepted", "final_success"):
                    continue
                lad = p.la(
                    ps.proof_step_index,
                    ic.informal_candidate_index,
                    la.lean_translation_attempt_index,
                )
                declarations_path = os.path.join(lad, "declarations.lean")
                placeholder_path = os.path.join(lad, "placeholder.lean")
                has_decl = "missing"
                if os.path.exists(declarations_path):
                    txt = open(declarations_path).read()
                    has_decl = "yes" if re.search(r"(?m)^\s*(theorem|lemma|def)\b", txt) else "empty"
                has_placeholder = "yes" if os.path.exists(placeholder_path) else "no"
                review = (
                    la.semantic_review_attempts[-1].verdict.lower()
                    if la.semantic_review_attempts
                    and la.semantic_review_attempts[-1].verdict
                    else la.semantic_review_attempts[-1].status
                    if la.semantic_review_attempts else "not_run"
                )
                print(f"{ps.proof_step_index:>4} {ic.informal_candidate_index:>4} "
                      f"{la.lean_translation_attempt_index:>4}  "
                      f"{la.attempt_kind:<18} {la.status:<34} "
                      f"{review:<11} {has_decl:<13} {has_placeholder}")


def _la_dir(root, i, j, k):
    return Paths(os.path.dirname(root), os.path.basename(root)).la(i, j, k)


def cmd_show(args):
    root = _run_root(args, args.problem_id)
    _load_state(root)  # existence check
    p = Paths(os.path.dirname(root), os.path.basename(root))
    icd = p.ic(args.step, args.candidate)
    lad = p.la(args.step, args.candidate, args.attempt)
    if not os.path.isdir(lad):
        sys.exit(f"no such attempt: proof_step_{args.step:03d}/informal_candidate_{args.candidate:03d}/lean4_attempt_{args.attempt:03d}")

    def dump(title, path):
        print(f"\n{'='*72}\n{title}  ({path})\n{'='*72}")
        print(open(path).read().rstrip() if os.path.exists(path) else "(missing)")

    dump("REASONING PROMPT", os.path.join(icd, "reasoning_prompt.md"))
    dump("INFORMAL STEP", os.path.join(icd, "informal_step.md"))
    reasoning_error = os.path.join(icd, "reasoning_call_error.txt")
    if os.path.exists(reasoning_error):
        dump("REASONING CALL ERROR", reasoning_error)
    dump("TRANSLATOR PROMPT", os.path.join(lad, "translator_prompt.md"))
    dump("RAW TRANSLATOR OUTPUT", os.path.join(lad, "raw_translator_output.md"))
    dump("PARSED declarations.lean", os.path.join(lad, "declarations.lean"))
    placeholder_path = os.path.join(lad, "placeholder.lean")
    if os.path.exists(placeholder_path):
        dump("PARSED placeholder.lean", placeholder_path)
    dump("PARSED body.lean", os.path.join(lad, "body.lean"))
    for title, name in (
        ("DECLARATION CHECK INPUT", "declaration_check_input.lean"),
        ("BODY CHECK INPUT", "body_check_input.lean"),
        ("FINAL CHECK INPUT", "final_check_input.lean"),
    ):
        source_path = os.path.join(lad, name)
        if os.path.exists(source_path):
            dump(title, source_path)
    reviews_root = os.path.join(lad, "semantic_reviews")
    if os.path.isdir(reviews_root):
        for name in sorted(os.listdir(reviews_root)):
            review_dir = os.path.join(reviews_root, name)
            if not os.path.isdir(review_dir):
                continue
            for title, filename in (
                ("SEMANTIC REVIEW PROMPT", "reviewer_prompt.md"),
                ("RAW SEMANTIC REVIEW OUTPUT", "raw_reviewer_output.md"),
                ("SEMANTIC REVIEW CALL ERROR", "reviewer_call_error.txt"),
            ):
                path = os.path.join(review_dir, filename)
                if os.path.exists(path):
                    dump(f"{title} ({name})", path)
    dump("compile.json", os.path.join(lad, "compile.json"))


def _find_attempt(st, step, candidate, attempt):
    for ps in st.proof_steps:
        if ps.proof_step_index != step:
            continue
        for ic in ps.informal_candidates:
            if ic.informal_candidate_index != candidate:
                continue
            for la in ic.lean_translation_attempts:
                if la.lean_translation_attempt_index == attempt:
                    return la
    return None


def _print_errors(errors):
    for error in errors:
        print(
            f"    {error.get('file', '')}:{error.get('line', 0)}:"
            f"{error.get('col', 0)} {error.get('code', '')}: "
            f"{error.get('message', '')[:100]}"
        )


def cmd_replay(args):
    """Re-run one saved parsed transaction without making an LLM call."""
    from midas.reconstructor import render_source
    from midas.structure import (
        body_contains_sorry,
        check_filled_placeholder,
        check_structure,
        declared_names,
        exploration_sorry_violations,
        extract_header,
        extract_placeholder_info,
    )
    from midas.verifier_client import make_verifier

    root = _run_root(args, args.problem_id)
    st = _load_state(root)
    p = Paths(os.path.dirname(root), os.path.basename(root))
    lad = p.la(args.step, args.candidate, args.attempt)
    if not os.path.isdir(lad):
        sys.exit(f"no such attempt: proof_step_{args.step:03d}/informal_candidate_{args.candidate:03d}/lean4_attempt_{args.attempt:03d}")

    la = _find_attempt(st, args.step, args.candidate, args.attempt)
    compile_path = os.path.join(lad, "compile.json")
    compile_data = (
        json.load(open(compile_path)) if os.path.exists(compile_path) else {}
    )
    original_status = compile_data.get(
        "attempt_status", la.status if la is not None else "unknown"
    )
    if original_status in ("translation_call_failed", "parse_error"):
        reason = (
            "the translator call returned no output"
            if original_status == "translation_call_failed"
            else "the translator response did not produce a valid parsed checkpoint"
        )
        sys.exit(
            f"attempt is not replayable: {reason}; use `show` to inspect it"
        )

    config_data = json.load(open(os.path.join(root, "config.json")))
    config = Config(**config_data)
    prelude = config.lean_prelude
    context = open(os.path.join(root, "input", "context.lean")).read()
    accepted = []
    previous_body = open(os.path.join(root, "input", "body_initial.lean")).read()
    for s in range(1, args.step):
        dp = os.path.join(p.accepted_ps(s), "declarations.lean")
        if os.path.exists(dp):
            accepted.append(open(dp).read())
        bp = os.path.join(p.accepted_ps(s), "body.lean")
        if os.path.exists(bp):
            previous_body = open(bp).read()

    declarations_path = os.path.join(lad, "declarations.lean")
    body_path = os.path.join(lad, "body.lean")
    if not os.path.exists(declarations_path) or not os.path.exists(body_path):
        sys.exit(
            "attempt is not replayable: parsed declarations.lean and body.lean "
            "artifacts are required; use `show` to inspect it"
        )
    cand_decl = open(declarations_path).read()
    cand_body = open(body_path).read()

    placeholder_path = os.path.join(lad, "placeholder.lean")
    attempt_kind = compile_data.get("attempt_kind")
    if not attempt_kind and os.path.exists(placeholder_path):
        attempt_kind = "hard_finalization"
    if not attempt_kind and la is not None:
        attempt_kind = la.attempt_kind
    attempt_kind = attempt_kind or "exploration"
    if attempt_kind not in (
        "exploration", "easy_finalization", "hard_finalization"
    ):
        sys.exit(f"attempt is not replayable: unknown attempt kind {attempt_kind!r}")

    problem_mode = st.problem_mode or config.problem_mode
    placeholder = ""
    initial_placeholder_path = os.path.join(root, "input", "placeholder.lean")
    placeholder_info = None
    if problem_mode == "hard":
        if not os.path.exists(initial_placeholder_path):
            sys.exit("attempt is not replayable: Hard Mode input placeholder is missing")
        initial_placeholder = open(initial_placeholder_path).read()
        try:
            placeholder_info = extract_placeholder_info(initial_placeholder)
        except Exception as error:
            sys.exit(f"attempt is not replayable: malformed saved input placeholder: {error}")
        if attempt_kind == "exploration":
            placeholder = initial_placeholder
        elif attempt_kind == "hard_finalization":
            if not os.path.exists(placeholder_path):
                sys.exit(
                    "attempt is not replayable: Hard finalization placeholder "
                    "artifact is missing"
                )
            placeholder = open(placeholder_path).read()

    header = st.formal_theorem_header
    if not header:
        header = extract_header(
            open(os.path.join(root, "input", "body_initial.lean")).read()
        )
    previous_names = []
    for declaration_block in accepted:
        previous_names.extend(declared_names(declaration_block))
    structure = check_structure(
        cand_decl, cand_body, header, previous_names
    )
    if attempt_kind == "exploration":
        sorry_violations = exploration_sorry_violations(
            previous_body, cand_body
        )
        structure.violations.extend(sorry_violations)
        structure.ok = structure.ok and not sorry_violations
    if attempt_kind != "exploration" and body_contains_sorry(cand_body):
        structure.violations.append("final theorem body contains `sorry`")
        structure.ok = False
    if attempt_kind == "hard_finalization":
        placeholder_structure = check_filled_placeholder(
            placeholder,
            st.placeholder_header or placeholder_info.header,
            st.placeholder_name or placeholder_info.name,
        )
        structure.violations.extend(placeholder_structure.violations)
        structure.ok = structure.ok and placeholder_structure.ok

    label = (
        f"proof_step_{args.step:03d}/"
        f"informal_candidate_{args.candidate:03d}/"
        f"lean4_attempt_{args.attempt:03d}"
    )
    print(
        f"replaying {label} (kind={attempt_kind}, backend={config.verifier_backend}, "
        f"prelude={len(prelude)} lines, {len(accepted)} prior accepted decls) "
        "— no LLM call\n"
    )
    print(f"original_status   : {original_status}")
    semantic_data = compile_data.get("semantic_review") or {}
    semantic_status = semantic_data.get("status", "not_run")
    semantic_verdict = semantic_data.get("verdict", "")
    print(
        "semantic_review : "
        + ((semantic_verdict or semantic_status) + " (saved; not replayed)")
    )
    print(f"structure_check   : {'passed' if structure.ok else 'failed'}")
    for violation in structure.violations:
        print(f"    {violation}")
    if not structure.ok:
        replay_accepted = False
        original_accepted = original_status in ("accepted", "final_success")
        print("=> REJECTED")
        print(
            "verdict_match     : "
            f"{'yes' if replay_accepted == original_accepted else 'no'}"
        )
        return

    verifier = make_verifier(config)
    final_ok = True
    final_result = None
    try:
        cp = verifier.check(
            prelude,
            context,
            accepted,
            cand_decl,
            cand_body,
            placeholder=placeholder,
            require_closed=(attempt_kind != "exploration"),
        )
        if cp.accepted and attempt_kind != "exploration":
            full = render_source(
                prelude,
                context,
                accepted,
                candidate_declarations=cand_decl,
                placeholder=placeholder,
                theorem_body=cand_body,
            ).text
            os.makedirs(p.tmp, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".lean",
                prefix="replay_",
                dir=p.tmp,
                delete=False,
            ) as replay_file:
                replay_file.write(full)
                replay_path = replay_file.name
            try:
                final_result = verifier.compile_full_file(replay_path)
            finally:
                try:
                    os.unlink(replay_path)
                except OSError:
                    pass
            final_ok = final_result.ok and not final_result.contains_sorry
    finally:
        verifier.close()

    print(f"declaration_check: {cp.declaration_check.status}")
    _print_errors(cp.declaration_check.errors)
    print(f"body_check       : {cp.body_check.status}")
    _print_errors(cp.body_check.errors)
    print(f"contains_sorry   : {cp.contains_sorry}")
    if final_result is not None:
        print(f"final_check      : {'passed' if final_ok else 'failed'}")
        _print_errors([
            {
                "file": error.file,
                "line": error.line,
                "col": error.col,
                "code": error.code,
                "message": error.message,
            }
            for error in final_result.errors
        ])
    semantic_accepted = semantic_status in ("not_run", "passed")
    replay_accepted = cp.accepted and final_ok and semantic_accepted
    original_accepted = original_status in ("accepted", "final_success")
    print(f"=> {'ACCEPTED (would advance)' if replay_accepted else 'REJECTED'}")
    print(
        "verdict_match     : "
        f"{'yes' if replay_accepted == original_accepted else 'no'}"
    )


def cmd_putnambench(args):
    """Dispatch `midas putnambench <tool> ...` to the PutnamBench modules (spec §1/§2/§3).
    `run`/`compile-gate` propagate their exit code (0 all-ok / 2 unresolved / 1 controller error)."""
    tool, rest = args.tool, args.rest
    if tool in ("scan", "prepare"):
        from midas.putnambench import prepare
        raise SystemExit(prepare.main([f"--{tool}"] + rest))
    if tool in ("setup", "preflight", "compile-gate"):
        from midas.putnambench import ec2
        raise SystemExit(ec2.main([tool] + rest))
    from midas.putnambench import runner
    raise SystemExit(runner.main([tool] + rest))   # init | run | status | report


def main(argv=None):
    ap = argparse.ArgumentParser(prog="midas", description="lemma-first Lean 4 proof-search loop")
    ap.add_argument("--runs-root", default="runs", help="directory holding run outputs (default: runs)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the loop on a problem (id or dir)")
    r.add_argument("problem_dir", metavar="problem",
                   help="problem id or directory, e.g. p4_n5_30 or problems/p4_n5_30")
    r.set_defaults(fn=cmd_run)

    rs = sub.add_parser(
        "resume",
        help="resume an interrupted call or the next unit after a runtime cutoff",
    )
    rs.add_argument("problem_id")
    rs.set_defaults(fn=cmd_resume)

    cr = sub.add_parser(
        "custom-resume",
        help="branch a run and retry an exact recorded pipeline call",
    )
    cr.add_argument("source_run_id")
    cr.add_argument("new_run_id")
    cr.add_argument("--step", type=int, required=True)
    cr.add_argument("--candidate", type=int, required=True)
    cr.add_argument(
        "--stage", choices=("reasoner", "translator", "reviewer"), required=True
    )
    cr.add_argument("--attempt", type=int)
    cr.add_argument("--reviewer-attempt", type=int)
    cr.set_defaults(fn=cmd_custom_resume)

    s = sub.add_parser("status", help="show a run's status + step summary")
    s.add_argument("problem_id"); s.set_defaults(fn=cmd_status)

    a = sub.add_parser("attempts", help="list translation attempts")
    a.add_argument("problem_id"); a.add_argument("--step", type=int, default=None)
    a.add_argument("--failed-only", action="store_true"); a.set_defaults(fn=cmd_attempts)

    sh = sub.add_parser("show", help="dump one attempt's prompts/outputs/artifacts")
    for name in ("problem_id",): sh.add_argument(name)
    for name in ("step", "candidate", "attempt"): sh.add_argument(name, type=int)
    sh.set_defaults(fn=cmd_show)

    rp = sub.add_parser("replay", help="recompile one checkpoint via verifier only (no LLM)")
    for name in ("problem_id",): rp.add_argument(name)
    for name in ("step", "candidate", "attempt"): rp.add_argument(name, type=int)
    rp.set_defaults(fn=cmd_replay)

    pb = sub.add_parser("putnambench", help="PutnamBench benchmark: prepare|scan|init|run|status|report")
    pb.add_argument("tool", choices=["setup", "preflight", "scan", "prepare", "compile-gate",
                                     "init", "run", "status", "report"])
    pb.add_argument("rest", nargs=argparse.REMAINDER)
    pb.set_defaults(fn=cmd_putnambench)

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
