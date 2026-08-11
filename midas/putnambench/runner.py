#!/usr/bin/env python3
"""
PutnamBench benchmark controller (spec §3/§4): resumable, campaigned, parallel runner over
prepared MIDAS instances, with an atomically-updated ledger and SEPARATE per-task reporting.

Real solving is done by `midas.cli run` (needs Lean 4.27.0 + Mathlib on the EC2). This module owns
the provider-agnostic controller: ledger / resume / campaigns / concurrency / exit codes / report.
The per-instance worker command is INJECTABLE (`--worker-cmd`) so the controller can be tested
without Lean or the models.

  init   --corpus <prepared_dir> --run-id ID [--select slice.txt] [--task both|proof_only|answer_synthesis]
  run    --run-id ID [--jobs N] [--campaigns N] [--timeout S] [--worker-cmd '...']
  status --run-id ID
  report --run-id ID

Exit codes (run): 0 = every runnable instance solved, 2 = bounded campaigns left unresolved, 1 = controller error.

SAFETY: --jobs > 1 in ONE checkout is unsafe for REAL runs until the §1 verifier refactor gives each
worker its own working dir (shared `.work/` race). Default is 1 (conservative, per spec); parallelism
across EC2s = run this per checkout over disjoint --select slices, then merge with `report`.
"""
import argparse, csv, glob, json, os, subprocess, sys, threading, time
from collections import Counter

QUEUED, RUNNING, SUCCESS, FAILED, INTERRUPTED, PREP_FAIL, NA = (
    "queued", "running", "final_success", "failed", "interrupted", "prepare_failed", "not_applicable")
TERMINAL_OK = {SUCCESS}
CARRIED = {NA, PREP_FAIL}
UNRESOLVED = {FAILED, INTERRUPTED, QUEUED, RUNNING}


def _atomic_write(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)   # atomic on POSIX


class Ledger:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.data = json.load(open(path)) if os.path.isfile(path) else {}

    def get(self, inst):
        return self.data.get(inst, {})

    def set(self, inst, **kw):
        with self.lock:
            self.data.setdefault(inst, {}).update(kw)
            _atomic_write(self.path, self.data)

    def event(self, evpath, **kw):
        with self.lock:
            with open(evpath, "a") as f:
                f.write(json.dumps(kw) + "\n")


def _cd(out, run_id):
    return os.path.join(out, run_id)


def cmd_init(args):
    cd = _cd(args.out, args.run_id)
    os.makedirs(cd, exist_ok=True)
    sel = set(open(args.select).read().split()) if args.select else None
    insts = {}
    for d in sorted(glob.glob(os.path.join(args.corpus, "*"))):
        bjp = os.path.join(d, "benchmark.json")
        if not os.path.isfile(bjp):
            continue
        bj = json.load(open(bjp))
        inst = os.path.basename(d)
        if sel is not None and inst not in sel:
            continue
        if args.task != "both" and bj.get("task") != args.task:
            continue
        ps = bj.get("prepare_status")
        status = NA if ps == "not_applicable" else PREP_FAIL if ps == "prepare_failed" else QUEUED
        insts[inst] = {"task": bj.get("task"), "problem_dir": os.path.abspath(d),
                       "status": status, "attempts": []}
    _atomic_write(os.path.join(cd, "manifest.json"),
                  {"run_id": args.run_id, "corpus": os.path.abspath(args.corpus),
                   "created": time.strftime("%Y-%m-%dT%H:%M:%S"), "count": len(insts)})
    _atomic_write(os.path.join(cd, "ledger.json"), insts)
    c = Counter(v["status"] for v in insts.values())
    print(f"init {args.run_id}: {len(insts)} instances ({dict(c)})")
    return 0


def _run_one(meta, attempt_dir, worker_cmd, timeout):
    os.makedirs(attempt_dir, exist_ok=True)
    pid = os.path.basename(meta["problem_dir"])
    cmd = worker_cmd.format(problem=meta["problem_dir"], runs_root=attempt_dir)
    env = os.environ.copy()
    env["MIDAS_WORK_DIR"] = os.path.join(attempt_dir, ".work")   # §1: isolate this worker's fresh-compile scratch
    try:
        subprocess.run(cmd, shell=True, timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return INTERRUPTED
    sp = os.path.join(attempt_dir, pid, "state.json")
    if os.path.isfile(sp):
        try:
            return json.load(open(sp)).get("status", FAILED)
        except Exception:
            return FAILED
    return FAILED


def cmd_run(args):
    cd = _cd(args.out, args.run_id)
    if not os.path.isfile(os.path.join(cd, "ledger.json")):
        print(f"no ledger for {args.run_id} — run `init` first", file=sys.stderr)
        return 1
    if args.jobs > 1:
        print(f"note: --jobs {args.jobs} — each worker gets its own MIDAS_WORK_DIR + its own warm Lean "
              f"process, so it is file-safe in one checkout. Ensure the box has RAM for {args.jobs} "
              f"resident Mathlib processes and inference sized for {args.jobs} concurrent generations.",
              file=sys.stderr)
    ledger = Ledger(os.path.join(cd, "ledger.json"))
    evpath = os.path.join(cd, "events.jsonl")
    for campaign in range(1, args.campaigns + 1):
        todo = [i for i, v in ledger.data.items()
                if v["status"] not in TERMINAL_OK and v["status"] not in CARRIED]
        if not todo:
            break
        print(f"campaign {campaign}/{args.campaigns}: {len(todo)} instances, jobs={args.jobs}")
        q = list(todo)
        qlock = threading.Lock()

        def worker():
            while True:
                with qlock:
                    if not q:
                        return
                    inst = q.pop(0)
                meta = ledger.get(inst)
                attempt_dir = os.path.join(cd, "attempts", f"c{campaign}", inst)  # fresh dir per campaign
                ledger.set(inst, status=RUNNING)
                ledger.event(evpath, t=time.time(), inst=inst, campaign=campaign, ev="start")
                st = _run_one(meta, attempt_dir, args.worker_cmd, args.timeout)
                atts = list(ledger.get(inst).get("attempts", []))
                atts.append({"campaign": campaign, "dir": attempt_dir, "status": st})
                ledger.set(inst, status=st, attempts=atts)
                ledger.event(evpath, t=time.time(), inst=inst, campaign=campaign, ev="end", status=st)

        threads = [threading.Thread(target=worker) for _ in range(max(1, args.jobs))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    unresolved = [i for i, v in ledger.data.items() if v["status"] in UNRESOLVED]
    code = 0 if not unresolved else 2
    print(f"run {args.run_id}: {dict(Counter(v['status'] for v in ledger.data.values()))}  exit={code}")
    return code


def cmd_status(args):
    p = os.path.join(_cd(args.out, args.run_id), "ledger.json")
    if not os.path.isfile(p):
        print(f"no run {args.run_id}", file=sys.stderr)
        return 1
    data = json.load(open(p))
    print(f"{args.run_id}: {dict(Counter(v['status'] for v in data.values()))}")
    return 0


def cmd_report(args):
    cd = _cd(args.out, args.run_id)
    data = json.load(open(os.path.join(cd, "ledger.json")))
    tasks = {}
    for inst, v in data.items():
        d = tasks.setdefault(v["task"] or "unknown",
                             dict(runnable=0, solved=0, failed=0, interrupted=0,
                                  not_applicable=0, prepare_failed=0))
        st = v["status"]
        if st == NA:
            d["not_applicable"] += 1
        elif st == PREP_FAIL:
            d["prepare_failed"] += 1
        else:
            d["runnable"] += 1
            d["solved"] += st == SUCCESS
            d["failed"] += st == FAILED
            d["interrupted"] += st == INTERRUPTED
    with open(os.path.join(cd, "results.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["instance", "task", "status", "attempts"])
        for inst, v in sorted(data.items()):
            w.writerow([inst, v["task"], v["status"], len(v.get("attempts", []))])
    with open(os.path.join(cd, "results.jsonl"), "w") as f:
        for inst, v in sorted(data.items()):
            f.write(json.dumps({"instance": inst, **v}) + "\n")
    lines = [f"# PutnamBench run `{args.run_id}`", ""]
    for t, d in sorted(tasks.items()):
        rate = 100 * d["solved"] / d["runnable"] if d["runnable"] else 0.0
        lines += [f"## {t}",
                  f"- runnable: **{d['runnable']}**",
                  f"- solved: **{d['solved']}**  ({rate:.1f}%)",
                  f"- failed: {d['failed']}   interrupted: {d['interrupted']}",
                  f"- not_applicable: {d['not_applicable']}   prepare_failed: {d['prepare_failed']}", ""]
    open(os.path.join(cd, "SUMMARY.md"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"wrote {cd}/SUMMARY.md, results.csv, results.jsonl")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    common = argparse.ArgumentParser(add_help=False)      # --out on every subcommand
    common.add_argument("--out", default="runs_putnam")
    pi = sub.add_parser("init", parents=[common])
    pi.add_argument("--corpus", required=True); pi.add_argument("--run-id", required=True)
    pi.add_argument("--select"); pi.add_argument("--task", default="both")
    pi.set_defaults(fn=cmd_init)
    pr = sub.add_parser("run", parents=[common])
    pr.add_argument("--run-id", required=True); pr.add_argument("--jobs", type=int, default=1)
    pr.add_argument("--campaigns", type=int, default=1); pr.add_argument("--timeout", type=float, default=10800)
    pr.add_argument("--worker-cmd",
                    default=f'{sys.executable} -m midas.cli --runs-root "{{runs_root}}" run "{{problem}}"')
    pr.set_defaults(fn=cmd_run)
    ps = sub.add_parser("status", parents=[common]); ps.add_argument("--run-id", required=True); ps.set_defaults(fn=cmd_status)
    rp = sub.add_parser("report", parents=[common]); rp.add_argument("--run-id", required=True); rp.set_defaults(fn=cmd_report)
    args = ap.parse_args(argv)
    try:
        return args.fn(args)
    except Exception as e:
        print(f"controller error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
