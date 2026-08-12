# Running PutnamBench

Benchmark MIDAS against **PutnamBench** — 672 formalized Putnam problems
(`github.com/trishullab/PutnamBench`), pinned to **Lean/Mathlib 4.27.0**.

Two independently-reported tasks (the Easy/Hard distinction is explained in the main
[README](README.md#adding-a-problem-of-your-own-requires-knowing-what-easy-mode-and-hard-mode-are)):

- **`proof_only`** = Easy Mode — prove the theorem with PutnamBench's official answer inlined.
- **`answer_synthesis`** = Hard Mode — the `*_solution` is left as a placeholder; the prover must
  *find the answer and prove it*. Only the **346** problems with a factored `*_solution` qualify;
  the other 326 are `proof_only` only.

All commands are `python3 -m midas.cli putnambench <tool> …`, from the repo root with the venv active.

---

## 0. Environment (once per machine)

PutnamBench uses `import Mathlib`, so it needs the **warm** backend. The setup is **exactly the
main README's steps 2–6**, with **one change: pin everything to `v4.27.0` instead of `v4.31.0`**
(PutnamBench is pinned to Lean/Mathlib 4.27.0). Specifically, while following the README:

- **Mathlib host** (README step 3): `lake +leanprover/lean4:v4.27.0 new mathlib_host math`, and set
  `rev = "v4.27.0"` for the `mathlib` require in `mathlib_host/lakefile.toml` before `lake exe cache get`.
- **Warm verifier** (README step 5): `echo "leanprover/lean4:v4.27.0" > warm-server/lean-toolchain`
  before `lake build warm`, then save the LEAN_PATH to `warm-server/mathlib_leanpath.txt` as the README shows.
- **Python deps + key**: README step 6, plus `OPENROUTER_API_KEY` in `~/.midas-mvp.env`.

Then confirm the box is ready:

```bash
python3 -m midas.cli putnambench preflight     # fails loudly if Lean != 4.27.0 or warm/Mathlib is missing
```

(`putnambench setup --revision <sha>` additionally clones the corpus into an ignored cache and records
a reproducibility manifest; the Mathlib/warm build above is the manual half.)

---

## 1. Prepare the problems

```bash
git clone https://github.com/trishullab/PutnamBench.git          # pin a commit for reproducibility
CORPUS=PutnamBench

python3 -m midas.cli putnambench prepare $CORPUS/lean4/src \
    --informal $CORPUS/informal/putnam.json \
    --out problems_putnam \
    --tasks proof_only               # or: answer_synthesis  |  proof_only,answer_synthesis
```

Writes `problems_putnam/putnam_<id>__<task>/` (same layout as any MIDAS problem — see the README's
Easy/Hard layouts — plus a `benchmark.json` with provenance). `putnambench scan $CORPUS/lean4/src
--informal …` gives a parse-coverage report without generating anything.

## 2. Compile-gate

```bash
python3 -m midas.cli putnambench compile-gate --corpus problems_putnam
```

Compiles every prepared instance under Mathlib 4.27.0 and flips `prepared_pending_compile →
runnable` (or `prepare_failed` with a diagnostic — never silently dropped).

## 3. Run

```bash
python3 -m midas.cli putnambench init --corpus problems_putnam --run-id myrun
python3 -m midas.cli putnambench run  --run-id myrun --jobs 8 --timeout 3600 --campaigns 1
```

- `--jobs N` — concurrent workers (each gets its own warm process + `MIDAS_WORK_DIR`, so it's file-safe).
- `--timeout S` — per-problem wall-clock cap; a problem still trying when it hits this → `interrupted`.
- `--campaigns C` — re-attempt unresolved instances C times (fresh dirs; never overwrites evidence).
- **Resumable:** re-running `run` skips solved instances and re-attempts `failed`/`interrupted`.

## 4. Status & report

```bash
python3 -m midas.cli putnambench status --run-id myrun
python3 -m midas.cli putnambench report --run-id myrun     # writes SUMMARY.md, results.csv, results.jsonl
```

`report` gives **separate** solve rates for `proof_only` and `answer_synthesis` (never combined).
`run` exit codes: **0** all solved, **2** unresolved remain, **1** controller error. A success means
the reconstructed final file compiles under Lean 4.27.0 with **no `sorry`**.

---

## 5. Splitting across people / machines

```bash
python3 bench/slice_gen.py --dir problems_putnam --workers 7 --out slices    # disjoint slices

# each person, own box + own API key + own run-id:
python3 -m midas.cli putnambench init --corpus problems_putnam --run-id alice --select slices/slice_00.txt
python3 -m midas.cli putnambench run  --run-id alice --jobs 8 --timeout 3600

python3 bench/aggregate.py collected/alice collected/bob …    # merge everyone's runs -> one solve rate
```

> 7 boxes divide the queue, but sharing one OpenRouter account means the **same** inference ceiling —
> use separate keys (or self-hosted inference) to actually go faster. Snapshot a configured instance
> into an **AMI** so each box skips the one-time setup.

## 6. Resource notes

- **Memory is not the wall.** Mathlib oleans are memory-mapped and **shared** across warm workers —
  10 concurrent workers measured at **~7 GB total** on a 124 GB box.
- **The wall is inference** — concurrent runs contend on the model API; `--jobs` past your throughput just queues.
- **Disk** ~50 GB (Mathlib cache + toolchain); **~16 vCPU** is plenty; **no GPU** (API models).
  The setup persists on the EBS volume across instance stop/start.

## Example (validated)

First 10 Putnam problems (the 1962 set), `proof_only`, `--jobs 10 --timeout 3600` on an
`r5.4xlarge`: **4 / 10 solved** (`1962_a4, a5, a6, b2`), 4 failed, 2 interrupted — each solve
genuinely Lean-4.27.0-kernel-verified.
