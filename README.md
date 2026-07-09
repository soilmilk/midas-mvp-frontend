# midas-mvp

A **lemma-first, bounded, linear proof-search loop** for Lean 4. Given an informal problem and a
Lean theorem header, it drives two LLMs — a **reasoner** (proposes one small proof step) and a
**translator** (renders it to Lean) — through a **verify → accept-or-retry** loop until the theorem
is proved with no `sorry`, or a budget is exhausted. Every step is checked by the real Lean compiler
in accumulated context. Implements `SPEC.md` (the design doc). MVP is complete (phases 1–5); see
`NOTES.md`, `PHASE4_REPORT.md`, and `LEMMA_FIRST_ANALYSIS.md` for build/run findings.

> **Training / modifying it:** the intended way to improve behavior is to evolve the two prompt files
> in `considerations/` from observed run failures — the metaoptimizing loop. **Read `HANDOFF.md`** for
> that workflow; this README is how to *run and extend* the system.

---

## How it works (30-second version)

```
informal problem + Lean header
        │
        ▼
  ReasoningAgent (gpt-5)         "here is one small next step + its proof"
        │
        ▼
  TranslationAgent (claude)      NEW DECLARATIONS + UPDATED THEOREM BODY (Lean)
        │
        ▼
  OutputParser → StructureChecker → VerifierClient (fresh `lean` per checkpoint)
        │                                    │
   parse_error / format_failed          declaration check (no sorry)
                                         body check (sorry allowed)
        │                                    │
        ▼                                    ▼
   retry w/ feedback                accept step (lemma folds in) → next step
                                    or, if body has no sorry → reconstruct
                                    the whole file & compile it independently
                                    → final_success
```

Two files evolve per step: a **declaration delta** (`declarations.lean`, new lemmas/defs, no
`sorry`) that accumulates, and the **full theorem body** (`body.lean`, `sorry` allowed until the
end). Only accepted artifacts are used to reconstruct the final proof.

---

## Prerequisites

1. **Lean 4 via elan** (`lean-toolchain` pins `leanprover/lean4:v4.31.0`; elan installs it):
   ```bash
   curl https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -sSf | sh
   source "$HOME/.elan/env"; elan --version
   ```
   The current problems are **Core/Std only — no Mathlib**, so every compile is ~0.3–0.6 s and
   nothing else needs installing on the Lean side.

2. **Python 3.9+** with:
   ```bash
   pip install pydantic openai
   ```
   (`openai` is used for *all* calls — both models route through OpenRouter's OpenAI-compatible API.)

3. **An OpenRouter API key** (for live `run`s only — the verifier and offline tests need no key).
   Store it **outside the repo** so it can never be committed:
   ```bash
   echo "export OPENROUTER_API_KEY='sk-or-...'" > ~/.midas-mvp.env && chmod 600 ~/.midas-mvp.env
   ```

---

## Quickstart

```bash
git clone https://github.com/soilmilk/midas-mvp.git && cd midas-mvp

# 1. sanity-check the verifier and the offline loop (NO API key needed)
python3 verifier/run_phase1.py          # verifier gate: 3 checkpoints + a negative test
python3 tests/test_offline.py           # deterministic pipeline on canned outputs
python3 tests/test_loop_offline.py      # full loop offline (retry -> final_success)

# 2. run a real problem (needs the key)
source ~/.midas-mvp.env
python3 -m midas.cli run problems/p1_sanity

# 3. inspect the run (no key needed)
python3 -m midas.cli status p1_sanity
python3 -m midas.cli attempts p1_sanity
```

---

## CLI reference

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
python3 -m midas.cli show p3_imo 4 1 1        # ps004 / candidate 1 / attempt 1
python3 -m midas.cli replay p3_imo 4 1 1      # reproduce that compile result offline
```

---

## Adding a problem

Create `problems/<id>/` with an `input/` dir and a `config.json`:

```
problems/<id>/
  input/
    informal_problem.md     # the natural-language statement
    context.lean            # definitions the theorem needs — NO imports (prelude supplies them)
    body_initial.lean       # theorem <name> ... := by\n  sorry   (must end the header in ':= by')
  config.json
  reference_solution.lean   # optional; documents provability (the loop never reads it)
```

Rules that matter:
- `context.lean` is **immutable** and contains **no imports** (put imports in `lean_prelude`).
- `body_initial.lean` must contain exactly one theorem whose header ends in **`:= by`** (the loader
  fails loudly otherwise). That header is stored and enforced **byte-for-byte** on every later body.
- Keep it **Core/Std-provable** unless you add a Mathlib prelude (which makes every compile slow —
  see "Mathlib" below).

`config.json` (SPEC §2):
```json
{
  "max_proof_steps": 8,
  "max_informal_candidates_per_proof_step": 3,
  "max_lean_translation_attempts_per_candidate": 3,
  "max_total_lean_attempts": 60,
  "max_runtime_seconds": 900,
  "lean_prelude": [],
  "reasoning_model": "openai/gpt-5",
  "translation_model": "anthropic/claude-sonnet-5",
  "reasoning_effort": "low"
}
```
- `lean_prelude` — **full Lean lines**, prepended verbatim (e.g. `"import Mathlib"`, `"set_option maxHeartbeats 0"`). Empty for Core/Std.
- `reasoning_effort` — `minimal | low | medium | high`. **Biggest latency lever**: gpt-5 is a reasoning model (≈1.5 s at `minimal`, ≈6 s at `low`, ≈10–13 s default). `minimal` = zero reasoning tokens (fast but shallow). See `NOTES.md`.

---

## Artifact layout (SPEC §6)

Everything a run produces (and everything the metaoptimizer feeds on) is under `runs/<id>/`:
```
runs/<id>/
  config.json  state.json
  input/            (copied inputs + context_check.json / initial_body_check.json)
  artifacts/proof_steps/psNNN/icNNN/{reasoning_prompt.md, informal_step.md}
                                    /laNNN/{translator_prompt.md, raw_translator_output.md,
                                            declarations.lean, body.lean, compile.json}
  accepted/psNNN/{declarations.lean, body.lean}     # the accepted path only
  final/{solution.lean, solution.md}                # on success
  failure/{failure_report.md, last_verified.lean, last_body.lean}   # on failure
```
`runs/` is git-ignored (it's generated). `compile.json` diagnostics are structured:
`{file, line, col, severity, code, message}` — `code` is the Lean diagnostic code (e.g.
`lean.unknownIdentifier`) for bucketing failures without regexing prose.

---

## Attempt statuses (SPEC §17)

`pending · parse_error · format_failed · lemma_failed · body_failed · accepted · final_success · final_reconstruction_failed`

- **`parse_error`** — raw model output couldn't be parsed into the two required sections.
- **`format_failed`** — parsed, but broke a structure rule (sorry in a declaration, header changed, repeated name, >1 theorem).
- **`lemma_failed` / `body_failed`** — the declaration / body compile failed.
- **`accepted`** — both compile, body still has `sorry` (progress).
- **`final_success`** — body has no `sorry` and the independently reconstructed `final/solution.lean` compiles clean.

---

## Training / modifying it

The system's behavior is shaped by two prompt files the models read on every call:
- `considerations/INFORMAL_REASONING_CONSIDERATIONS.md` — rules for the reasoner (§9).
- `considerations/FORMAL_TRANSLATION_CONSIDERATIONS.md` — rules for the translator (§10).

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
NOTES.md                    spec-review ambiguities + all build/run findings
PHASE4_REPORT.md            results of the 3 live runs
LEMMA_FIRST_ANALYSIS.md     why lemma-first is skipped + fixes
EXTERNAL_AGENT_SUGGESTIONS.md   proposed prompt patches (metaoptimizer output)
PENDING_PATTERNS.md         patterns seen but not yet promoted to rules
considerations/             the two prompt files the models read (edit these to train)
midas/                      the loop: models, agents, parser, structure, verifier_client,
                            reconstructor, artifacts, loop, cli
verifier/                   checkpoint_builder.py (fresh `lean` per checkpoint) + phase-1 gate
problems/                   p1_sanity, p2_lemma, p3_imo (+ toy), each input/ + config.json
tests/                      offline pipeline + offline loop tests (no API key)
```

---

## Notes on scope

- **Fresh `lean` per checkpoint** is a deliberate choice (fast for Core/Std). A warm/persistent
  verifier backend — needed if you switch to a Mathlib prelude, where every cold `import Mathlib`
  costs ~15–40 s — is a future swap behind the same `VerifierClient` interface (see the sibling
  `midas_proof_verifier` project). Not built here.
- **Mathlib:** set `lean_prelude` to `["import Mathlib", ...]` and point `LEAN_PATH` at a prebuilt
  Mathlib; expect each checkpoint to take tens of seconds with the current fresh-compile backend.
- The **Metaoptimizer agent itself** (SPEC §20) is out of MVP scope — the loop logs everything it
  would consume; `HANDOFF.md` describes building it.
