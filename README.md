# Midas MVP

A **lemma-first, bounded, linear proof-search loop** for Lean 4. Given an informal problem and a
Lean theorem header, it drives two LLMs — a **reasoner** (proposes one small proof step) and a
**translator** (renders it to Lean) — through a **verify → accept-or-retry** loop until the theorem
is proved with no `sorry`, or a budget is exhausted. Every step is checked by the real Lean compiler
in accumulated context. Implements `SPEC.md` (the design doc). MVP is complete (phases 1–5); see
`NOTES.md`, `PHASE4_REPORT.md`, and `LEMMA_FIRST_ANALYSIS.md` for build/run findings.

> **Training / modifying it:** the intended way to improve behavior is to evolve the two prompt files
> in `considerations/` from observed run failures — the metaoptimizing loop. **Read the diagram below`** for
> that workflow; this README is how to *run and extend* the system.

---


![Alt Text](midas-mvp.png)


---

## Setup & running (Ubuntu environment - setup WSL if you're on Windows)

**1. Add your OpenRouter API key**
You will need to create your own key and buy credits on OpenRouter ($10 should be enough), or alternatively reach out to Daniel to share his API key.
It should never be committed to the repo. 
Only `run` needs it.

  ```bash
  echo "export OPENROUTER_API_KEY='sk-or-...'" > ~/.midas-mvp.env && chmod 600 ~/.midas-mvp.env
  ```

**2. Install Lean 4 (via elan - the Lean toolchain manager):**
  ```bash
  curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh
  ```
  Choose option 1) Proceed with installation (default)
  After installation, restart your shell or run:

  ```bash
  source "$HOME/.elan/env"
  ```
  Verify that `elan` is available:

  ```bash
  elan --version
  ```

**3. Install a prebuilt Mathlib for v4.31.0:**
  ```bash
  # Alternatively to these two commands, you can reuse any mathlib project if you have it
  cd /desired/mathlib_project/location # simple option: cd ~
  lake +leanprover/lean4:v4.31.0 new mathlib_host math # shouldn't take more than 2 minutes

  cd mathlib_host
  # make sure that lean-toolchain contains "leanprover/lean4:v4.31.0"
  # make sure that lakefile.toml has a "[[require]]" section with name = "mathlib" and rev = "v4.31.0"
  lake exe cache get        # downloads prebuilt Mathlib oleans (minutes, not hours)
  lake build

  ```

**4. Clone, and install the toolchain on first `lean` call.**
```bash
# Assuming that you're still on the mathlib_host folder
cd ..
git clone https://github.com/soilmilk/midas-mvp.git
cd midas-mvp                    # <-- run everything below from here
lean --version                  # Should print something that starts with "Lean (version 4.31.0."
```

**5. Build the warm verifier and connect it to Mathlib.**
Future problems are expected to use `import Mathlib`, so the warm backend is part of normal setup.
The warm executable is built from this repo; Mathlib oleans stay in your local `mathlib_host`.
```bash
cd warm-server
lake build warm
cd ..

# Save the Mathlib LEAN_PATH where midas-mvp's warm backend will read it automatically.
(cd path/to/mathlib_host && lake env printenv LEAN_PATH) > warm-server/mathlib_leanpath.txt
# ex: (cd ~/mathlib_host && lake env printenv LEAN_PATH) > warm-server/mathlib_leanpath.txt
```

**6. Install Python deps** (Python 3.9+). `openai` handles *all* calls — both models route through
OpenRouter's OpenAI-compatible API.
```bash
# Assuming that you're still on the midas-mvp folder
python3 -m venv .venv  # if it's a new EC2, might need to run 'sudo apt update' before that
source .venv/bin/activate
pip install pydantic openai
```


**7. Testing the install — NO key needed** (proves Lean + Python are wired up correctly).
```bash
python3 verifier/run_phase1.py       # must end: PHASE 1 GATE: PASS
python3 tests/test_offline.py        # must end: PHASE 2 OFFLINE SPINE: PASS
python3 tests/test_loop_offline.py   # must end: OFFLINE LOOP: PASS
```

**8. Run a real Lean 4 problem** (needs your OpenRouter key).
```bash
source ~/.midas-mvp.env

# The first real run. As the proof progresses, take a look at runs/p4_n5_30/artifacts/proof_steps.
# You will see the loop happening in real time! 
python3 -m midas.cli run p4_n5_30

# Alternatively, you can run this command from another terminal:
python3 -m midas.cli status p4_n5_30
```


---

## Adding a problem of your own

Create `problems/<id>/` with an `input/` dir and a `config.json`:

```
problems/<id>/
  input/
    informal_problem.md     # the natural-language statement
    context.lean            # definitions the theorem needs — NO imports (prelude supplies them)
    body_initial.lean       # theorem <name> ... := by\n  sorry   (must end the header in ':= by')
  config.json
  reference_solution.lean   # OPTIONAL; documents provability (the loop never reads it)
```

Rules that matter:
- `context.lean` are the definitions/object that the theorem uses. Contains **no imports** (put imports in `lean_prelude`).
- `body_initial.lean` must contain exactly one theorem whose header ends in **`:= by`** (the loader
  fails loudly otherwise). That header is stored and enforced **byte-for-byte** on every later body.
- See problems/p3_imo for an example.

- example for `config.json`:
```json
{
  "max_proof_steps": 8,
  "max_informal_candidates_per_proof_step": 3,
  "max_lean_translation_attempts_per_candidate": 3,
  "max_total_lean_attempts": 60,
  "max_runtime_seconds": 900,
  "lean_prelude": [
    "import Mathlib",
    "import Aesop",
    "set_option maxHeartbeats 0",
    "open BigOperators Real Nat Topology Rat"
  ],
  "reasoning_model": "openai/gpt-5",
  "translation_model": "anthropic/claude-sonnet-5",
  "reasoning_effort": "low",
  "verifier_backend": "warm"
}
```
- `lean_prelude` — **full Lean lines**, prepended verbatim (e.g. `"import Mathlib"`, `"set_option maxHeartbeats 0"`).
- `reasoning_effort` — `minimal | low | medium | high`. **Biggest latency lever**: gpt-5 is a reasoning model (≈1.5 s at `minimal`, ≈6 s at `low`, ≈10–13 s default). `minimal` = zero reasoning tokens (fast but shallow). See `NOTES.md`.

--- 


### Running a Mathlib problem

After setup, Mathlib problems should use `verifier_backend: "warm"`. The backend defaults to the
in-repo binary at `warm-server/.lake/build/bin/warm` and reads Mathlib from
`warm-server/mathlib_leanpath.txt`.

```bash
source ~/.midas-mvp.env
python3 -m midas.cli run problems/<mathlib_problem>
```

If you did not save `warm-server/mathlib_leanpath.txt`, set the path for the current shell instead:
```bash
export MIDAS_WARM_LEAN_PATH="$(cd /path/to/your/mathlib_project && lake env printenv LEAN_PATH)"
```


### Troubleshooting
| symptom | cause | fix |
|---|---|---|
| `No module named 'midas'` | not in the repo root | `cd` into `midas-mvp` (the folder with `midas/`) and run from there — **the most common issue** |
| `No module named 'pydantic'` / `'openai'` | deps missing | `pip install pydantic openai` |
| `lean: command not found` / wrong version | elan not set up / not on PATH | rerun the elan installer, `source "$HOME/.elan/env"`, then `lean --version` inside the repo |
| warning `OPENROUTER_API_KEY not set`, then live calls fail | key not loaded | `source ~/.midas-mvp.env` in the *same* shell (your own key) |
| warm run fails before starting, or Lean reports `unknown module prefix 'Mathlib'` | Mathlib `LEAN_PATH` is missing or points at the wrong version | rerun `(cd ~/mathlib_host && lake env printenv LEAN_PATH) > warm-server/mathlib_leanpath.txt`; ensure Mathlib is **v4.31.0** |
| seems to hang on a call | OpenRouter throttling (account tier) | the loop caps each call by the remaining runtime budget; raise your tier or lower `reasoning_effort` |

---

## Artifact layout

Everything a run produces (and everything the metaoptimizer feeds on) is under `runs/<id>/`:

<img width="297" height="417" alt="image" src="https://github.com/user-attachments/assets/8e99997f-233d-4ce7-a011-e3ca2eda9b7e" />

---

## Attempt statuses

`pending · parse_error · format_failed · lemma_failed · body_failed · accepted · final_success · final_reconstruction_failed`

- **`parse_error`** — raw model output couldn't be parsed into the two required sections.
- **`format_failed`** — parsed, but broke a structure rule (sorry in a declaration, header changed, repeated name, >1 theorem).
- **`lemma_failed` / `body_failed`** — the declaration / body compile failed.
- **`accepted`** — both compile, body still has `sorry` (progress).
- **`final_success`** — body has no `sorry` and the independently reconstructed `final/solution.lean` compiles clean.

---

## CLI reference

> ⚠️ **Run every command from the repo root** — the `midas-mvp/` folder that contains `midas/`.
> `python3 -m midas.cli` imports the `midas` package from the current directory; from anywhere else
> you get `ModuleNotFoundError: No module named 'midas'`.

All commands are `python3 -m midas.cli <cmd>`. Runs are written under `runs/<problem_id>/`
(override the location with `--runs-root DIR`).

| command | what it does |
|---|---|
| `run <problem_dir>` | Run the loop on a problem. **Needs `OPENROUTER_API_KEY`.** Writes the full artifact tree + `state.json`. |
| `status <problem_id>` | Status, theorem header, stats, and a per-proof-step summary. |
| `attempts <problem_id> [--step N] [--failed-only]` | Table of every translation attempt and its status; the `declarations?` column shows whether the step introduced a lemma. |
| `show <problem_id> <step> <cand> <attempt>` | Dump one attempt's reasoning prompt, translator prompt, **raw model output**, parsed `declarations.lean`/`body.lean`, and `compile.json`. |
| `replay <problem_id> <step> <cand> <attempt>` | **Recompile that one checkpoint via the verifier only — no LLM call.** For debugging a failure without burning API calls. |

Examples:
```bash
python3 -m midas.cli attempts p2_lemma --failed-only
python3 -m midas.cli show p3_imo 4 1 1        # proof_step_004 / candidate 1 / attempt 1
python3 -m midas.cli replay p3_imo 4 1 1      # reproduce that compile result offline
```

---

## Training / modifying it

The system's behavior is shaped by two prompt files the models read on every call:
- `considerations/INFORMAL_REASONING_CONSIDERATIONS.md` — rules for the reasoner (§9).
- `considerations/FORMAL_TRANSLATION_CONSIDERATIONS.md` — rules for the translator (§10).

NOTE: When testing/running proofs, please put your findings here instead of directly editing the prompts, we will accumulate all of y'alls feedback and then edit accordingly.
https://docs.google.com/document/d/1dXjaZKxNOavIGCYk2uyY2fPjsBhnSYR5mQ_5L5mlsu0/edit?usp=sharing

**To improve it, edit those files** based on failures you see in `runs/`. The disciplined process
for doing that (batch review, when to add a rule, versioning, guardrails) is the *metaoptimizing
loop* — **see `HANDOFF.md`**, plus `EXTERNAL_AGENT_SUGGESTIONS.md` (proposed edits, seeded from the
Phase-4 run) and `PENDING_PATTERNS.md` (patterns seen but not yet promoted to rules).

**Known limitation to work on first:** the models skip the "lemma-first" style and prove directly in
the body; prompt edits alone won't fully fix it. `LEMMA_FIRST_ANALYSIS.md` explains why and what to
change (structurally, not just in prompts).

---

## Repo map

```
SPEC.md*                    the design doc (external; not committed)
README.md                   this file
HANDOFF.md                  how the team trains/evolves it (metaoptimizing loop)
INTEGRATION.md              how the in-repo warm verifier backend works
NOTES.md                    spec-review ambiguities + all build/run findings
PHASE4_REPORT.md            results of the 3 live runs
LEMMA_FIRST_ANALYSIS.md     why lemma-first is skipped + fixes
EXTERNAL_AGENT_SUGGESTIONS.md   proposed prompt patches (metaoptimizer output)
PENDING_PATTERNS.md         patterns seen but not yet promoted to rules
considerations/             the two prompt files the models read (edit these to train)
midas/                      the loop: models, agents, parser, structure, verifier_client,
                            reconstructor, artifacts, loop, cli
verifier/                   checkpoint_builder.py (fresh `lean` per checkpoint) + phase-1 gate
warm-server/                Lean/Lake warm verifier executable for Mathlib-heavy checks
problems/                   p1_sanity, p2_lemma, p3_imo (+ toy), each input/ + config.json
tests/                      offline pipeline + offline loop tests (no API key)
```

---

## Notes on scope

- **Pluggable verifier backend.** `VerifierClient` runs the §14 checkpoint semantics through a
  swappable `VerifierBackend`: `fresh` (default — a `lean` subprocess per checkpoint, fast for
  Core/Std) or `warm` (routes to the in-repo `warm-server` executable, which keeps
  Mathlib resident and pays `import Mathlib` once). Select with `config.verifier_backend`; see
  **[INTEGRATION.md](INTEGRATION.md)**.
- **Mathlib:** future problems should use `lean_prelude` with `"import Mathlib"` and
  `verifier_backend: "warm"`. Setup builds `warm-server` and saves the Mathlib `LEAN_PATH`; with
  the `fresh` backend, a Mathlib checkpoint takes tens of seconds each.
- The **Metaoptimizer agent itself** (SPEC §20) is out of MVP scope — the loop logs everything it
  would consume; `HANDOFF.md` describes building it.
