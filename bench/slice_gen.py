#!/usr/bin/env python3
"""
Split a problem set into K disjoint slices — one file per worker/EC2.

Each slice file is a plain list of problem ids (one per line) that `batch_run.py --list`
consumes. Slices are guaranteed disjoint and complete (every problem assigned exactly once).

Examples:
  # 21 EC2s over all imported putnam problems
  python bench/slice_gen.py --dir problems --prefix putnam_ --workers 21 --out bench/slices
  # cap each slice at 11 problems (=> as many slices as needed)
  python bench/slice_gen.py --dir problems --prefix putnam_ --per 11 --out bench/slices
  # named per person/ec2
  python bench/slice_gen.py --dir problems --prefix putnam_ --workers 3 --names alice_a,alice_b,alice_c
"""
import argparse, glob, math, os, sys


def all_ids(dir_, prefix):
    ids = sorted(os.path.basename(d) for d in glob.glob(os.path.join(dir_, "*"))
                 if os.path.isfile(os.path.join(d, "config.json")))
    return [i for i in ids if i.startswith(prefix)] if prefix else ids


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="problems")
    ap.add_argument("--list", help="read ids from a file instead of scanning --dir")
    ap.add_argument("--prefix", default="")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--workers", type=int, help="number of slices")
    g.add_argument("--per", type=int, help="max problems per slice")
    ap.add_argument("--mode", choices=["contiguous", "roundrobin"], default="roundrobin",
                    help="roundrobin balances hard/easy across slices (default); contiguous keeps ranges")
    ap.add_argument("--out", default="bench/slices")
    ap.add_argument("--names", help="comma-separated slice names (optional)")
    args = ap.parse_args()

    ids = [l.strip() for l in open(args.list) if l.strip() and not l.startswith("#")] if args.list \
        else all_ids(args.dir, args.prefix)
    if not ids:
        sys.exit("no problems found (check --dir/--prefix/--list)")

    K = args.workers if args.workers else max(1, math.ceil(len(ids) / args.per))
    slices = [[] for _ in range(K)]
    if args.mode == "roundrobin":
        for i, pid in enumerate(ids):
            slices[i % K].append(pid)
    else:
        size = math.ceil(len(ids) / K)
        for k in range(K):
            slices[k] = ids[k * size:(k + 1) * size]

    os.makedirs(args.out, exist_ok=True)
    names = (args.names.split(",") if args.names else [f"slice_{k:02d}" for k in range(K)])
    for k in range(K):
        nm = names[k] if k < len(names) else f"slice_{k:02d}"
        path = os.path.join(args.out, nm + ".txt")
        open(path, "w").write(("\n".join(slices[k]) + "\n") if slices[k] else "")
        span = f"  [{slices[k][0]} … {slices[k][-1]}]" if slices[k] else "  (empty)"
        print(f"{path}: {len(slices[k])} problems{span}")

    flat = [p for s in slices for p in s]
    assert len(flat) == len(set(flat)) == len(ids), "SLICING BUG: overlap or missing problems!"
    print(f"\n{len(ids)} problems → {K} slices, disjoint + complete ✓  (out dir: {args.out})")


if __name__ == "__main__":
    main()
