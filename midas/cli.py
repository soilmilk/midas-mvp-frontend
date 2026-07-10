#!/usr/bin/env python3
"""
midas-mvp CLI (SPEC Phase 5). Usable without reading source:

  python3 -m midas.cli run <problem>          # id or dir: p4_n5_30  OR  problems/p4_n5_30
  python3 -m midas.cli status <problem_id>
  python3 -m midas.cli attempts <problem_id> [--step N] [--failed-only]
  python3 -m midas.cli show <problem_id> <step> <candidate> <attempt>
  python3 -m midas.cli replay <problem_id> <step> <candidate> <attempt>

`run` needs OPENROUTER_API_KEY (source ~/.midas-mvp.env). `replay` recompiles a single
checkpoint via the verifier only — no LLM call — for debugging without burning API calls.
Runs live under ./runs/<problem_id>/ (override with --runs-root).
"""
from __future__ import annotations
import argparse, glob, json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from midas.artifacts import StateManager, Paths
from midas.models import ProofRunState


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


def cmd_run(args):
    from midas.loop import run_problem
    if not os.environ.get("OPENROUTER_API_KEY"):
        print("warning: OPENROUTER_API_KEY not set — live agents will fail. `source ~/.midas-mvp.env`",
              file=sys.stderr)
    state = run_problem(_resolve_problem_dir(args.problem_dir), runs_root=os.path.abspath(args.runs_root))
    print(f"{state.problem_id}: {state.status}"
          + (f" ({state.failure_reason})" if state.failure_reason else "")
          + f"  | accepted={state.stats.accepted_proof_steps} llm={state.stats.total_llm_calls} "
            f"compiles={state.stats.total_lean_compiles} attempts={state.stats.total_lean_attempts} "
            f"runtime={state.stats.runtime_seconds:.0f}s")


def cmd_status(args):
    st = _load_state(_run_root(args, args.problem_id))
    print(f"problem : {st.problem_id}")
    print(f"status  : {st.status}" + (f"  ({st.failure_reason})" if st.failure_reason else ""))
    print(f"header  : {st.formal_theorem_header!r}")
    s = st.stats
    print(f"stats   : accepted_steps={s.accepted_proof_steps} llm_calls={s.total_llm_calls} "
          f"lean_compiles={s.total_lean_compiles} lean_attempts={s.total_lean_attempts} "
          f"runtime={s.runtime_seconds:.0f}s")
    print("steps   :")
    for ps in st.proof_steps:
        n_cand = len(ps.informal_candidates)
        n_att = sum(len(c.lean_translation_attempts) for c in ps.informal_candidates)
        print(f"  proof_step_{ps.proof_step_index:03d}  {ps.status:<14} candidates={n_cand} attempts={n_att}")


def cmd_attempts(args):
    st = _load_state(_run_root(args, args.problem_id))
    print(f"{'step':>4} {'cand':>4} {'att':>4}  {'status':<26} declarations?")
    print("-" * 60)
    for ps in st.proof_steps:
        if args.step and ps.proof_step_index != args.step:
            continue
        for ic in ps.informal_candidates:
            for la in ic.lean_translation_attempts:
                if args.failed_only and la.status in ("accepted", "final_success"):
                    continue
                has_decl = ""
                if la.declarations_path and os.path.exists(la.declarations_path):
                    txt = open(la.declarations_path).read()
                    import re
                    has_decl = "yes" if re.search(r"(?m)^\s*(theorem|lemma|def)\b", txt) else "empty"
                print(f"{ps.proof_step_index:>4} {ic.informal_candidate_index:>4} "
                      f"{la.lean_translation_attempt_index:>4}  {la.status:<26} {has_decl}")


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
    dump("TRANSLATOR PROMPT", os.path.join(lad, "translator_prompt.md"))
    dump("RAW TRANSLATOR OUTPUT", os.path.join(lad, "raw_translator_output.md"))
    dump("PARSED declarations.lean", os.path.join(lad, "declarations.lean"))
    dump("PARSED body.lean", os.path.join(lad, "body.lean"))
    dump("compile.json", os.path.join(lad, "compile.json"))


def cmd_replay(args):
    """Recompile a single checkpoint via the verifier only (no LLM)."""
    from midas.verifier_client import VerifierClient
    root = _run_root(args, args.problem_id)
    _load_state(root)
    p = Paths(os.path.dirname(root), os.path.basename(root))
    lad = p.la(args.step, args.candidate, args.attempt)
    if not os.path.isdir(lad):
        sys.exit(f"no such attempt: proof_step_{args.step:03d}/informal_candidate_{args.candidate:03d}/lean4_attempt_{args.attempt:03d}")

    config = json.load(open(os.path.join(root, "config.json")))
    prelude = config.get("lean_prelude", [])
    context = open(os.path.join(root, "input", "context.lean")).read()
    # accumulated accepted declarations from steps strictly before this one
    accepted = []
    for s in range(1, args.step):
        dp = p.accepted_ps(s) + "/declarations.lean"
        if os.path.exists(dp):
            accepted.append(open(dp).read())
    cand_decl = open(os.path.join(lad, "declarations.lean")).read() if os.path.exists(os.path.join(lad, "declarations.lean")) else ""
    cand_body = open(os.path.join(lad, "body.lean")).read() if os.path.exists(os.path.join(lad, "body.lean")) else ""

    print(f"replaying proof_step_{args.step:03d}/informal_candidate_{args.candidate:03d}/lean4_attempt_{args.attempt:03d} "
          f"(prelude={len(prelude)} lines, {len(accepted)} prior accepted decls) — verifier only\n")
    cp = VerifierClient().check(prelude, context, accepted, cand_decl, cand_body)
    print(f"declaration_check: {cp.declaration_check.status}")
    for e in cp.declaration_check.errors:
        print(f"    {e['file']}:{e['line']}:{e['col']} {e.get('code','')}: {e['message'][:100]}")
    print(f"body_check       : {cp.body_check.status}")
    for e in cp.body_check.errors:
        print(f"    {e['file']}:{e['line']}:{e['col']} {e.get('code','')}: {e['message'][:100]}")
    print(f"contains_sorry   : {cp.contains_sorry}")
    print(f"=> {'ACCEPTED (would advance)' if cp.accepted else 'REJECTED'}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="midas", description="lemma-first Lean 4 proof-search loop")
    ap.add_argument("--runs-root", default="runs", help="directory holding run outputs (default: runs)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run the loop on a problem (id or dir)")
    r.add_argument("problem_dir", metavar="problem",
                   help="problem id or directory, e.g. p4_n5_30 or problems/p4_n5_30")
    r.set_defaults(fn=cmd_run)

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

    args = ap.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
