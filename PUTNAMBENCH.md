# Running PutnamBench

End-to-end guide for benchmarking MIDAS against **PutnamBench** (672 formalized Putnam
problems, `github.com/trishullab/PutnamBench`, pinned to **Lean/Mathlib 4.27.0**).

All commands are `python3 -m midas.cli putnambench <tool> …`, run from the repo root with the
venv active. Two independently-reported tasks:

- **`proof_only`** (Easy Mode) — prove the theorem with PutnamBench's official answer inlined.
- **`answer_synthesis`** (Hard Mode) — the `*_solution` is left as a placeholder; the prover must
  *find the answer AND prove it*. Only the **346** problems that carry a factored `*_solution`
  are eligible; the other 326 are `proof_only` only.

---

## 0. One-time environment setup (Lean 4.27.0 + Mathlib + warm verifier)

PutnamBench is `import Mathlib`, so you need the **warm** backend. This is heavy but done once per
machine. Requirements: a Linux box with **≈50 GB disk** and enough RAM (see §6 — memory is *not*
the wall). **No GPU needed** — the models run via the OpenRouter API.

```bash
# 1) Lean 4.27.0 via elan
curl -sSf https://elan.lean-lang.org/elan-init.sh | sh -s -- -y --default-toolchain none
source ~/.elan/env
elan toolchain install leanprover/lean4:v4.27.0
elan default    leanprover/lean4:v4.27.0

# 2) Python deps
python3 -m venv .venv && ./.venv/bin/pip install -q pydantic openai

# 3) Build the warm verifier (Lean-only — no Mathlib build dependency, ~1 min)
echo "leanprover/lean4:v4.27.0" > warm-server/lean-toolchain
( cd warm-server && lake build warm )        # -> warm-server/.lake/build/bin/warm

# 4) Mathlib 4.27.0 cache (the runtime LEAN_PATH the warm process loads)
lake +leanprover/lean4:v4.27.0 new mathlib_host math
#   pin mathlib to v4.27.0: add  rev = "v4.27.0"  under [[require]] name = "mathlib" in
#   mathlib_host/lakefile.toml, then:
( cd mathlib_host && lake update && lake exe cache get )          # downloads prebuilt oleans (~min)
( cd mathlib_host && lake env printenv LEAN_PATH ) > warm-server/mathlib_leanpath.txt

# 5) API key
echo 'export OPENROUTER_API_KEY=sk-or-...' > ~/.midas-mvp.env
source ~/.midas-mvp.env

# 6) Verify the box is ready (fails loudly if Lean != 4.27.0 or warm/Mathlib missing)
python3 -m midas.cli putnambench preflight
```

The warm backend auto-finds `warm-server/.lake/build/bin/warm` and `warm-server/mathlib_leanpath.txt`,
so no per-problem config is needed. (`putnambench setup --revision <sha>` clones the corpus into an
ignored cache and records an env manifest; the Mathlib/warm build above is the manual half.)

---

## 1. Get the corpus & prepare problems

```bash
# clone the benchmark (pin a commit for reproducibility)
git clone https://github.com/trishullab/PutnamBench.git
CORPUS=PutnamBench

# (optional) coverage check — how many parse cleanly
python3 -m midas.cli putnambench scan $CORPUS/lean4/src --informal $CORPUS/informal/putnam.json

# generate MIDAS problem dirs (both tasks, or pick one)
python3 -m midas.cli putnambench prepare $CORPUS/lean4/src \
    --informal $CORPUS/informal/putnam.json \
    --out problems_putnam \
    --tasks proof_only               # or: answer_synthesis  |  proof_only,answer_synthesis
```

This writes `problems_putnam/putnam_<id>__<task>/` (each with `input/…` + a `benchmark.json`
carrying provenance and a `prepared_pending_compile` status).

## 2. Compile-gate (admit only what actually compiles)

```bash
python3 -m midas.cli putnambench compile-gate --corpus problems_putnam
```

Compiles every prepared instance under Mathlib 4.27.0 and flips `prepared_pending_compile →
runnable` (or `prepare_failed`, with a diagnostic — never silently dropped).

## 3. Run the benchmark

```bash
# register a run (optionally restrict to a slice — see §5)
python3 -m midas.cli putnambench init --corpus problems_putnam --run-id myrun

# solve, N problems in parallel; each problem capped at --timeout seconds
python3 -m midas.cli putnambench run --run-id myrun --jobs 8 --timeout 3600 --campaigns 1
```

- `--jobs N` — concurrent workers (file-safe: each gets its own warm process + `MIDAS_WORK_DIR`).
- `--timeout S` — per-problem wall-clock cap (a problem still trying when it hits this → `interrupted`).
- `--campaigns C` — re-attempt unresolved instances C times (fresh dirs; never overwrites evidence).
- **Resumable:** re-running `run` skips solved instances and re-attempts `failed`/`interrupted`.

## 4. Status & report

```bash
python3 -m midas.cli putnambench status --run-id myrun
python3 -m midas.cli putnambench report --run-id myrun     # writes SUMMARY.md, results.csv, results.jsonl
```

`report` gives **separate** solve rates for `proof_only` and `answer_synthesis` (never combined),
plus failed / interrupted / not_applicable counts. Exit codes on `run`: **0** all-solved, **2**
unresolved remain, **1** controller error. Success = the reconstructed final file compiles under
Lean 4.27.0 with **no `sorry`**.

---

## 5. Splitting across people / machines

Parallelize by handing each person a disjoint slice; each runs on their own box, then merge.

```bash
# split the corpus into 7 disjoint slice files
python3 bench/slice_gen.py --dir problems_putnam --workers 7 --out slices

# each person, on their own box (own API key, own run-id):
python3 -m midas.cli putnambench init --corpus problems_putnam --run-id alice --select slices/slice_00.txt
python3 -m midas.cli putnambench run  --run-id alice --jobs 8 --timeout 3600

# collect each person's runs_putnam/<run-id>/ into one place, then merge:
python3 bench/aggregate.py collected/alice collected/bob …     # -> one overall solve rate
```

> Note: 7 boxes multiply prover machines and divide the queue, but if everyone shares one
> OpenRouter account they hit the **same** inference ceiling. Use separate keys (or self-hosted
> inference) to actually go faster. To avoid re-setup on every box, snapshot a configured
> instance into an **AMI** and launch from it.

---

## 6. Resource guidance

- **Memory is not the wall.** A warm+Mathlib process is ~6 GB RSS, but Mathlib's oleans are
  **memory-mapped and shared** across workers — 10 concurrent workers measured at **~7 GB total**
  on a 124 GB box. You can run many workers.
- **The wall is inference** — concurrent runs contend on the model API (latency climbs under load).
  `--jobs` beyond your inference throughput just queues.
- **Disk:** ~50 GB (Mathlib cache + toolchain). **CPU:** ~16 vCPU is plenty. **No GPU** (API models).
- Stop/terminate the instance when idle — the setup persists on the EBS volume across stop/start.

---

## Example (validated)

First 10 Putnam problems (the 1962 set), `proof_only`, `--jobs 10 --timeout 3600` on an
`r5.4xlarge`: **4 / 10 solved** (`1962_a4, a5, a6, b2`), 4 failed, 2 interrupted — each solve
genuinely Lean-4.27.0-kernel-verified.
