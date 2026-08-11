#!/usr/bin/env python3
"""
PutnamBench EC2-side steps (spec §1 setup + §2 compile-gate).

These invoke Lean/Mathlib and are meant to run on the configured 4.27.0 EC2. The compile gate
REUSES MIDAS's own loader + InputValidator, so admitting an instance is byte-identical to the loop's
own input check (compile context + initial body [+ placeholder for Hard Mode]).

  setup        --revision <sha> [--cache .pb_cache] [--warm-binary P] [--lean-path-file P]
  preflight    [--manifest .pb_cache/env_manifest.json]
  compile-gate --corpus <prepared_dir>        # flips prepared_pending_compile -> runnable | prepare_failed
"""
import argparse, glob, hashlib, json, os, subprocess, sys, time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _sh(cmd):
    return subprocess.run(cmd, shell=True, text=True, capture_output=True)


def cmd_setup(args):
    """Clone/pin the corpus to an IMMUTABLE SHA in an ignored cache and record an env manifest.
    (Building 4.27.0 Mathlib + the 4.27.0 warm verifier follows the README; this records/pins them.)"""
    cache = os.path.abspath(args.cache)
    os.makedirs(cache, exist_ok=True)
    corpus = os.path.join(cache, "putnam-bench")
    if not os.path.isdir(os.path.join(corpus, ".git")):
        r = _sh(f'git clone https://github.com/trishullab/PutnamBench.git "{corpus}"')
        if r.returncode:
            print(r.stderr, file=sys.stderr); return 1
    _sh(f'git -C "{corpus}" fetch --all --tags')
    sha = (_sh(f'git -C "{corpus}" rev-parse "{args.revision}"').stdout or "").strip()
    if not sha:
        print(f"cannot resolve revision {args.revision!r}", file=sys.stderr); return 1
    _sh(f'git -C "{corpus}" checkout --detach {sha}')
    manifest = {
        "corpus_repo": "trishullab/PutnamBench",
        "corpus_sha": sha,
        "corpus_src": os.path.join(corpus, "lean4", "src"),
        "corpus_informal": os.path.join(corpus, "informal", "putnam.json"),
        "lean_version": (_sh("lean --version").stdout or "").strip(),
        "warm_binary": args.warm_binary or os.path.join(REPO, "warm-server", ".lake", "build", "bin", "warm"),
        "lean_path_file": args.lean_path_file or os.path.join(REPO, "warm-server", "mathlib_leanpath.txt"),
        "midas_git": (_sh(f'git -C "{REPO}" rev-parse --short HEAD').stdout or "").strip(),
        "midas_dirty": bool((_sh(f'git -C "{REPO}" status --porcelain').stdout or "").strip()),
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    mp = os.path.join(cache, "env_manifest.json")
    json.dump(manifest, open(mp, "w"), indent=2)
    print(f"setup: corpus@{sha[:10]}  lean={manifest['lean_version'].split(',')[0]}  manifest={mp}")
    return cmd_preflight(argparse.Namespace(manifest=mp, expect_lean="4.27.0"))


def cmd_preflight(args):
    """Fail loudly (before any model calls) if the recorded env is missing or mismatched."""
    m = json.load(open(args.manifest))
    bad = []
    if args.expect_lean not in m.get("lean_version", ""):
        bad.append(f"Lean is {m.get('lean_version')!r}, expected {args.expect_lean}")
    if not (m.get("warm_binary") and os.path.exists(m["warm_binary"])):
        bad.append(f"warm binary missing: {m.get('warm_binary')}")
    if not (m.get("lean_path_file") and os.path.isfile(m["lean_path_file"])):
        bad.append(f"Mathlib LEAN_PATH file missing: {m.get('lean_path_file')}")
    if bad:
        print("PREFLIGHT FAILED — fix before any model calls:", file=sys.stderr)
        for b in bad:
            print("  - " + b, file=sys.stderr)
        return 1
    print(f"preflight OK: Lean {args.expect_lean} + warm verifier + Mathlib LEAN_PATH present")
    return 0


def cmd_compile_gate(args):
    """Compile each prepared instance with the pinned verifier (reusing MIDAS's InputValidator) and
    flip prepared_pending_compile -> runnable | prepare_failed. Never silently omits an instance."""
    from midas.problem import load_problem, InputValidator
    from midas.verifier_client import make_verifier
    runnable = failed = skipped = 0
    for d in sorted(glob.glob(os.path.join(args.corpus, "*"))):
        bjp = os.path.join(d, "benchmark.json")
        if not os.path.isfile(bjp):
            continue
        bj = json.load(open(bjp))
        if bj.get("prepare_status") != "prepared_pending_compile":
            skipped += 1
            continue
        try:
            prob = load_problem(d)
            verifier = make_verifier(prob.config)
            vr = InputValidator(verifier).validate(prob)
            try:
                verifier.close()
            except Exception:
                pass
            ok, reason = bool(vr.ok), ("" if vr.ok else getattr(vr, "reason", "compile_failed"))
        except Exception as e:
            ok, reason = False, f"{type(e).__name__}: {e}"
        bj["prepare_status"] = "runnable" if ok else "prepare_failed"
        if not ok:
            bj["compile_reason"] = reason
        json.dump(bj, open(bjp, "w"), indent=2)
        runnable += ok
        failed += (not ok)
        print(f"  {'OK ' if ok else 'XX '}{os.path.basename(d)}" + ("" if ok else f"  ({reason[:90]})"))
    print(f"compile-gate: {runnable} runnable, {failed} prepare_failed, {skipped} skipped")
    return 0 if failed == 0 else 2


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    st = sub.add_parser("setup")
    st.add_argument("--revision", required=True); st.add_argument("--cache", default=".pb_cache")
    st.add_argument("--warm-binary"); st.add_argument("--lean-path-file")
    st.set_defaults(fn=cmd_setup)
    pf = sub.add_parser("preflight")
    pf.add_argument("--manifest", default=".pb_cache/env_manifest.json")
    pf.add_argument("--expect-lean", default="4.27.0")
    pf.set_defaults(fn=cmd_preflight)
    cg = sub.add_parser("compile-gate")
    cg.add_argument("--corpus", required=True)
    cg.set_defaults(fn=cmd_compile_gate)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
