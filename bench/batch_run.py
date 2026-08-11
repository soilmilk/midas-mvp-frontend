#!/usr/bin/env python3
"""
Batch runner for Midas Prover — run a slice of problems sequentially, resumably,
with a pass/fail scoreboard.

SAFE-BY-DESIGN: this runs problems ONE AT A TIME within a checkout, so it never
triggers the shared-.work/ race. Parallelism = run this in N SEPARATE checkouts
(one per person), each over a disjoint slice. See the README section on parallel runs.

Examples:
  # explicit ids
  python bench/batch_run.py --problems t3_bool t4_le p1_sanity
  # a slice of all problems (sorted), e.g. Alice takes the first 50
  python bench/batch_run.py --dir problems --start 0 --end 50 --runs-root runs_alice
  # ids from a file (one per line)
  python bench/batch_run.py --list slice_alice.txt --runs-root runs_alice

Resume: a problem is skipped if its run already reached a terminal state
(final_success always; failed too, unless --retry-failed).
"""
import argparse, csv, glob, json, os, subprocess, sys, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBLEMS = os.path.join(ROOT, "problems")
PY = os.path.join(ROOT, ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable
TERMINAL = {"final_success", "failed"}


def resolve_ids(args):
    if args.problems:
        return list(args.problems)
    if args.list:
        return [l.strip() for l in open(args.list) if l.strip() and not l.startswith("#")]
    if args.dir:
        allp = sorted(os.path.basename(d) for d in glob.glob(os.path.join(args.dir, "*"))
                      if os.path.isfile(os.path.join(d, "config.json")))
        return allp[args.start:args.end]
    sys.exit("give one of --problems / --list / --dir")


def prior_status(runs_root, pid):
    p = os.path.join(runs_root, pid, "state.json")
    if not os.path.isfile(p):
        return None
    try:
        return json.load(open(p)).get("status")
    except Exception:
        return None


def budget(pid):
    cfg = os.path.join(PROBLEMS, pid, "config.json")
    try:
        return json.load(open(cfg)).get("max_runtime_seconds", 3600)
    except Exception:
        return 3600


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--problems", nargs="*")
    ap.add_argument("--list")
    ap.add_argument("--dir")
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--end", type=int, default=None)
    ap.add_argument("--runs-root", default="runs")
    ap.add_argument("--results", default=None, help="CSV scoreboard (default: <runs-root>/_batch_results.csv)")
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--timeout-slack", type=int, default=120, help="grace seconds beyond max_runtime_seconds before killing a hung run")
    args = ap.parse_args()

    runs_root = os.path.abspath(args.runs_root)
    os.makedirs(runs_root, exist_ok=True)
    results = args.results or os.path.join(runs_root, "_batch_results.csv")
    ids = resolve_ids(args)

    new = not os.path.isfile(results)
    fh = open(results, "a", newline="")
    w = csv.writer(fh)
    if new:
        w.writerow(["ts", "problem", "status", "accepted_steps", "loop_runtime_s", "wall_s"])
        fh.flush()

    solved = attempted = skipped = 0
    print(f"batch: {len(ids)} problems → runs-root={runs_root}  scoreboard={results}\n")
    for i, pid in enumerate(ids, 1):
        pdir = os.path.join(PROBLEMS, pid)
        tag = f"[{i}/{len(ids)}] {pid}"
        if not os.path.isdir(pdir):
            print(f"{tag}: MISSING problem dir — skipping")
            w.writerow([int(time.time()), pid, "missing", 0, 0, 0]); fh.flush()
            continue
        st = prior_status(runs_root, pid)
        if st == "final_success" or (st == "failed" and not args.retry_failed):
            print(f"{tag}: skip (already {st})")
            skipped += 1
            continue

        attempted += 1
        t0 = time.time()
        logp = os.path.join(runs_root, pid + "_batch.log")
        os.makedirs(runs_root, exist_ok=True)
        cmd = [PY, "-m", "midas.cli", "--runs-root", runs_root, "run", pdir]
        try:
            with open(logp, "w") as lg:
                subprocess.run(cmd, cwd=ROOT, env=os.environ.copy(), stdout=lg,
                               stderr=subprocess.STDOUT, timeout=budget(pid) + args.timeout_slack)
            status = prior_status(runs_root, pid) or "unknown"
        except subprocess.TimeoutExpired:
            status = "timeout_killed"
        wall = round(time.time() - t0, 1)

        steps = rt = 0
        sp = os.path.join(runs_root, pid, "state.json")
        if os.path.isfile(sp):
            try:
                s = json.load(open(sp)); steps = s["stats"].get("accepted_proof_steps", 0)
                rt = round(s["stats"].get("runtime_seconds", 0), 1)
            except Exception:
                pass
        if status == "final_success":
            solved += 1
        w.writerow([int(time.time()), pid, status, steps, rt, wall]); fh.flush()
        mark = "OK " if status == "final_success" else "XX " if status == "failed" else "-- "
        print(f"{tag}: {mark} {status}  ({steps} steps, {wall}s wall)   [{solved} solved so far]")

    fh.close()
    print(f"\ndone: {solved} solved / {attempted} attempted ({skipped} skipped as already-done). "
          f"Scoreboard: {results}")


if __name__ == "__main__":
    main()
