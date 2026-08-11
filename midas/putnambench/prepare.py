#!/usr/bin/env python3
"""
PutnamBench -> MIDAS import/normalization (spec §2), the PARSING half.

This module contains the comment/string-aware top-level scanner that splits a PutnamBench
`lean4/src/*.lean` file into: imports, opens, an optional factored `*_solution` abbrev
(with its answer comment), and the single target theorem. Per spec, it REJECTS ambiguous
files (multiple theorems, multiple solution decls, no terminal `:= sorry`, …) instead of guessing.

Normalization into MIDAS problem dirs + the pinned-verifier compile gate build on top of this
(next steps); this file is standalone-runnable for coverage testing against the real corpus:

    python midas/putnambench/prepare.py --scan <corpus>/lean4/src --informal <corpus>/informal/putnam.json
"""
from __future__ import annotations
import argparse, json, os, re, sys
from dataclasses import dataclass, field, asdict
from typing import Optional, List


def mask(text: str) -> str:
    """Return `text` with Lean line comments (--), nestable block comments (/- -/), and
    string literals replaced by spaces (newlines preserved). Length-preserving, so offsets
    in the masked string map 1:1 back to the original — used only to LOCATE top-level tokens."""
    res = list(text)
    i, n = 0, len(text)
    state, depth, esc = "code", 0, False
    while i < n:
        c = text[i]
        two = text[i:i + 2]
        if state == "code":
            if two == "--":
                state = "line"; res[i] = res[i + 1] = " "; i += 2; continue
            if two == "/-":
                state = "block"; depth = 1; res[i] = res[i + 1] = " "; i += 2; continue
            if c == '"':
                state = "str"; res[i] = " "; i += 1; continue
            i += 1; continue
        if state == "line":
            if c == "\n": state = "code"
            else: res[i] = " "
            i += 1; continue
        if state == "block":
            if two == "/-": depth += 1; res[i] = res[i + 1] = " "; i += 2; continue
            if two == "-/":
                depth -= 1; res[i] = res[i + 1] = " "; i += 2
                if depth == 0: state = "code"
                continue
            if c != "\n": res[i] = " "
            i += 1; continue
        if state == "str":
            if esc: esc = False; res[i] = " "; i += 1; continue
            if c == "\\": esc = True; res[i] = " "; i += 1; continue
            if c == '"': state = "code"; res[i] = " "; i += 1; continue
            if c != "\n": res[i] = " "
            i += 1; continue
    return "".join(res)


@dataclass
class Parsed:
    ok: bool
    reason: str = ""
    imports: List[str] = field(default_factory=list)
    opens: List[str] = field(default_factory=list)
    solution_name: Optional[str] = None
    solution_decl: Optional[str] = None      # full original `abbrev …_solution … := sorry`
    solution_answer: Optional[str] = None     # the answer text from the trailing comment
    theorem_name: Optional[str] = None
    theorem_head: Optional[str] = None        # original theorem signature up to (not incl.) `:=`
    has_solution: bool = False
    # byte offsets into the ORIGINAL text (for normalization)
    thm_start: int = -1
    sol_start: int = -1
    sol_assign: int = -1
    sol_block_end: int = -1   # first real content after the abbrev + its answer comment


_TOP = lambda kw: re.compile(r"(?m)^(?:noncomputable\s+)?" + kw + r"\s+([A-Za-z_][\w']*)")


def parse_file(text: str) -> Parsed:
    m = mask(text)

    imports = [text[a.start():a.end()].strip()
               for a in re.finditer(r"(?m)^\s*import\s+.*$", m)]
    opens = [text[a.start():a.end()].strip()
             for a in re.finditer(r"(?m)^\s*open\s+.*$", m)]

    thms = list(_TOP("theorem").finditer(m))
    if len(thms) == 0:
        return Parsed(False, "no top-level theorem")
    if len(thms) > 1:
        return Parsed(False, f"multiple theorems ({len(thms)})")

    abbrevs = list(_TOP("abbrev").finditer(m))
    sol_matches = [a for a in abbrevs if a.group(1).endswith("_solution")]
    if len(sol_matches) > 1:
        return Parsed(False, "multiple _solution abbrevs")

    p = Parsed(True)
    p.imports, p.opens = imports, opens

    thm = thms[0]
    thm_start = thm.start()
    # The proof hole is the TERMINAL ':= sorry' at end-of-file — NOT the first ':=', which may be a
    # `let ⟨…⟩ := putnam_…_solution` destructuring inside the statement.
    mterm = re.search(r":=\s*(?:by\b\s*)?sorry\s*\Z", m)
    if not mterm or mterm.start() < thm_start:
        return Parsed(False, "no terminal ':= sorry' after theorem")
    p.theorem_name = thm.group(1)
    p.theorem_head = text[thm_start:mterm.start()].rstrip()   # signature, up to (not incl.) ':='
    p.thm_start = thm_start

    if sol_matches:
        p.has_solution = True
        sa = sol_matches[0]
        p.solution_name = sa.group(1)
        s_assign = m.find(":=", sa.start())
        if s_assign < 0 or s_assign > thm_start:
            return Parsed(False, "solution abbrev has no ':=' before theorem")
        p.solution_decl = text[sa.start():s_assign + 2].rstrip() + " sorry"
        p.sol_start = sa.start()
        p.sol_assign = s_assign
        # answer = the comment block between the abbrev's `:= sorry` and the next real decl
        after = m.find("sorry", s_assign)
        pos = after + 5
        while pos < thm_start and m[pos] in " \t\r\n":
            pos += 1
        p.sol_block_end = pos
        gap_orig = text[after + 5: thm_start]
        ans = []
        for ln in gap_orig.splitlines():
            s = ln.strip()
            if s.startswith("--"):
                ans.append(s[2:].strip())
            elif s.startswith("/-") or s.startswith("-/") or s == "":
                continue
            elif s.startswith("/--"):
                break  # docstring
        p.solution_answer = "\n".join(ans).strip() or None
    return p


def scan(src_dir: str, informal_path: Optional[str]) -> dict:
    informal = {}
    if informal_path and os.path.isfile(informal_path):
        for e in json.load(open(informal_path)):
            informal[e["problem_name"]] = e

    files = sorted(f for f in os.listdir(src_dir) if f.endswith(".lean"))
    total = ok = with_sol = with_informal = 0
    reasons = {}
    samples = []
    rejects = []
    for f in files:
        name = f[:-5]
        p = parse_file(open(os.path.join(src_dir, f)).read())
        total += 1
        if p.ok:
            ok += 1
            with_sol += p.has_solution
            with_informal += name in informal
            if len(samples) < 3 and p.has_solution:
                samples.append((name, p))
        else:
            reasons[p.reason.split("(")[0].strip()] = reasons.get(p.reason.split("(")[0].strip(), 0) + 1
            if len(rejects) < 12:
                rejects.append((name, p.reason))
    return {"total": total, "ok": ok, "with_solution": with_sol,
            "with_informal": with_informal, "reasons": reasons,
            "samples": samples, "rejects": rejects, "informal_count": len(informal)}


_DOCSTRING_TAIL = re.compile(r"/--.*?-/\s*\Z", re.S)


def _rewrite_solution_header(text: str, sol_start: int, sol_assign: int) -> str:
    """`[noncomputable] abbrev NAME : TYPE`  ->  `noncomputable def NAME : TYPE` (name/type kept)."""
    hdr = text[sol_start:sol_assign].strip()
    return re.sub(r"^(?:noncomputable\s+)?(?:abbrev|def)\b", "noncomputable def", hdr, count=1)


def normalize(text: str, p: Parsed, task: str) -> dict:
    """Return {ok, reason, prelude:[...], context, body_initial, placeholder|None} for one task.
    Compiling is NOT done here (needs Lean 4.27.0) — that is the EC2-side admission gate."""
    if not p.ok:
        return {"ok": False, "reason": p.reason}
    if task == "answer_synthesis" and not p.has_solution:
        return {"ok": False, "reason": "not_applicable: no factored *_solution to synthesize"}

    prelude = list(p.imports) + list(p.opens)          # matches the working putnam2025A2 template
    body_initial = p.theorem_head + " := by\n  sorry"

    ctx = text[:p.thm_start]                            # everything before the theorem
    placeholder = None
    if p.has_solution:
        if task == "answer_synthesis":                 # HARD: solution becomes a fill-me placeholder
            placeholder = _rewrite_solution_header(text, p.sol_start, p.sol_assign) + " := by\n  sorry"
            ctx = ctx[:p.sol_start] + ctx[p.sol_block_end:]     # drop abbrev + answer comment, keep aux
        else:                                          # EASY: inline the official answer
            if not p.solution_answer:
                return {"ok": False, "reason": "prepare_failed: proof_only needs an answer but none found"}
            ctx = ctx[:p.sol_assign] + " := " + p.solution_answer + "\n\n" + ctx[p.sol_block_end:]
    ctx = re.sub(r"(?m)^[ \t]*(?:import|open)\b.*$", "", ctx)   # imports/opens -> prelude
    ctx = _DOCSTRING_TAIL.sub("", ctx)                          # drop the theorem's now-floating docstring
    ctx = re.sub(r"\n{3,}", "\n\n", ctx).strip()
    return {"ok": True, "reason": "", "prelude": prelude, "context": ctx,
            "body_initial": body_initial, "placeholder": placeholder}


def _cfg(task: str, prelude: list) -> dict:
    hard = task == "answer_synthesis"
    return {"problem_mode": "hard" if hard else "easy",
            "max_proof_steps": 80 if hard else 40,
            "max_informal_candidates_per_proof_step": 3,
            "max_lean_translation_attempts_per_candidate": 3,
            "max_total_lean_attempts": 400 if hard else 200,
            "max_runtime_seconds": 10800 if hard else 3600,
            "lean_prelude": prelude,
            "reasoning_model": "openai/gpt-5", "translation_model": "anthropic/claude-sonnet-5",
            "reasoning_effort": "high" if hard else "medium",
            "verifier_backend": "warm"}


def generate(src_dir, informal_path, out_dir, tasks, commit="", limit=None):
    informal = {e["problem_name"]: e for e in json.load(open(informal_path))} if informal_path else {}
    files = sorted(f for f in os.listdir(src_dir) if f.endswith(".lean"))
    if limit:
        files = files[:limit]
    made = collections_counter()
    for f in files:
        name = f[:-5]
        raw = open(os.path.join(src_dir, f)).read()
        p = parse_file(raw)
        for task in tasks:
            pid = f"putnam_{name.replace('putnam_', '')}__{task}"
            norm = normalize(raw, p, task)
            pdir = os.path.join(out_dir, pid)
            inp = os.path.join(pdir, "input")
            os.makedirs(inp, exist_ok=True)
            meta = {"problem_name": name, "task": task, "corpus_commit": commit,
                    "lean_version": "v4.27.0", "source_file": f,
                    "source_sha256": _sha(raw), "has_solution": p.has_solution,
                    "informal_tags": informal.get(name, {}).get("tags", [])}
            if not norm["ok"]:
                meta["prepare_status"] = ("not_applicable" if norm["reason"].startswith("not_applicable")
                                          else "prepare_failed")
                meta["reason"] = norm["reason"]
                json.dump(meta, open(os.path.join(pdir, "benchmark.json"), "w"), indent=2)
                made[meta["prepare_status"]] += 1
                continue
            open(os.path.join(inp, "informal_problem.md"), "w").write(
                informal.get(name, {}).get("informal_statement", name) + "\n")
            open(os.path.join(inp, "context.lean"), "w").write(norm["context"] + ("\n" if norm["context"] else ""))
            open(os.path.join(inp, "body_initial.lean"), "w").write(norm["body_initial"] + "\n")
            if norm["placeholder"] is not None:
                open(os.path.join(inp, "placeholder.lean"), "w").write(norm["placeholder"] + "\n")
            json.dump(_cfg(task, norm["prelude"]), open(os.path.join(pdir, "config.json"), "w"), indent=2)
            meta["prepare_status"] = "prepared_pending_compile"   # EC2 compile-gate flips this to 'runnable'
            meta["normalized_body_sha256"] = _sha(norm["body_initial"])
            json.dump(meta, open(os.path.join(pdir, "benchmark.json"), "w"), indent=2)
            made["prepared"] += 1
    return made


def _sha(s):
    import hashlib
    return hashlib.sha256(s.encode()).hexdigest()[:16]


def collections_counter():
    import collections
    return collections.Counter()


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", help="path to lean4/src (coverage report)")
    ap.add_argument("--prepare", help="path to lean4/src (generate MIDAS problem dirs)")
    ap.add_argument("--informal", help="path to informal/putnam.json")
    ap.add_argument("--out", default="problems_putnam")
    ap.add_argument("--tasks", default="proof_only,answer_synthesis")
    ap.add_argument("--commit", default="")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args(argv)
    if args.scan:
        r = scan(args.scan, args.informal)
        print(f"corpus: {r['total']} files | informal entries: {r['informal_count']}")
        print(f"PARSED OK: {r['ok']}/{r['total']}  ({100*r['ok']/max(1,r['total']):.1f}%)")
        print(f"  answer_synthesis-eligible (has *_solution): {r['with_solution']}")
        print(f"  paired with an informal statement: {r['with_informal']}")
        print(f"REJECTED: {r['total']-r['ok']}  reasons: {r['reasons']}")
    elif args.prepare:
        made = generate(args.prepare, args.informal, args.out, args.tasks.split(","),
                        commit=args.commit, limit=args.limit)
        print(f"generated into {args.out}: {dict(made)}")
    else:
        ap.error("give --scan or --prepare")
    return 0


if __name__ == "__main__":
    sys.exit(main())
