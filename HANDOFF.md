# HANDOFF — training & evolving midas-mvp

For the team picking this up. The Easy and Hard Mode loops work end to end; the job now is to
**make the system lemma-first in practice** and improve its success rate. Hard Mode itself is a
separate concern: it adds an unresolved definition, preserves it during English-space exploration,
and fills it atomically with the final theorem. This doc explains how to train the models, where the
evidence lives, and the structural change the earlier run data says you need.

## The mental model (kitchen)

- **gpt-5 (reasoner)** = the cook who decides *what dish to make next*.
- **claude (translator)** = the cook who *plates it to the exact house spec*.
- **Lean (verifier)** = the health inspector — every plate passes or is sent back, no opinions.
- **`considerations/*.md`** = the recipe cards on the wall (what the cooks read every time).
- **The metaoptimizer** = the head chef who, after a shift, reviews the tickets that got **sent
  back** and rewrites the recipe cards.

"Training" here is **rewriting recipe cards from failures** — not retraining the cooks (no weights).

## How you train it (the metaoptimizing loop — SPEC §20, METAOPTIMIZING.md)

1. **Run a batch** (not one problem — single runs are too noisy). Everything is logged under
   `runs/<id>/` (see README "Artifact layout"): every prompt, every raw model output, every
   `compile.json`, the `state.json`, and `failure/failure_report.md`.
2. **Classify the failures.** Taxonomy (METAOPTIMIZING.md §2): (1) non-compiling output,
   (2) structural violation, (3) over-complex informal step, (4) incorrect informal step.
   Route **1–2 → `FORMAL_TRANSLATION_CONSIDERATIONS.md`**, **3–4 → `INFORMAL_REASONING_CONSIDERATIONS.md`**.
   The `compile.json` `code` field and the attempt `status` (`parse_error` vs `format_failed` vs
   `lemma_failed` vs `body_failed`) bucket these for you.
3. **Decide whether to edit.** Only add a rule when the **same pattern appears 3+ times in a batch**,
   or it caused a **fatal failure**. Otherwise log it to `PENDING_PATTERNS.md` and promote it later.
4. **Edit one rule at a time**, additive by default, **version-bump + changelog** each edit (so you
   can correlate a `considerations_version` with a change in success rate). Cap a section at ~40
   rules, then consolidate.
5. **Guardrails:** never delete a rule just because it went quiet; tag domain-specific rules so they
   don't pollute the general set; **escalate to a human** if a new rule contradicts 2+ existing ones
   or the fatal-failure rate exceeds ~20%.

Proposed edits get written (with motivation) to **`EXTERNAL_AGENT_SUGGESTIONS.md`**; a developer
applies them to the `considerations/` files. For the MVP this application is **manual on purpose** —
you stay in the loop.

## The catch — why recipe-card edits alone won't make it lemma-first

The head chef only learns from **sent-back plates**. But the shortcut we care about — cooks throwing
everything in **one pan** (proving directly in the theorem body) instead of making **base sauces
first** (reusable lemmas) — *usually passes the inspector*. In Phase 4, **0 of 19 accepted steps
used a lemma**; two of three problems still succeeded body-only, and the one that couldn't be
one-panned (`p2_lemma`) failed by flailing, never reaching for the lemma it needed.

So a shortcut that works is never sent back, so no recipe-card note stamps it out. **To enforce
lemma-first you have to change what the inspector checks, not the recipe cards.** Concretely
(`LEMMA_FIRST_ANALYSIS.md` maps each to its cause):

- **Invert the translation contract:** require a non-empty `NEW DECLARATIONS` (a named lemma) on
  every non-final step; restrict the body to *citing* accepted lemmas.
- **Add a progress metric:** reject a step whose goal-at-`sorry` / sorry-structure didn't improve —
  this classifies body-only flailing as a *failure*, which then *feeds* the metaoptimizer a signal
  it can act on (and is the same signal a reward function would use if you later go to RL/training).
- **Reframe the reasoning prompt** from "next step" to "next *lemma* to prove."
- **Remove body-only escapes** in problems meant to test the path (e.g. `p3_imo`'s `congrArg`).

The first two are code changes to `midas/loop.py` + `midas/structure.py` (a `lemma_first` /
`require_declarations` config flag); the last two are prompt/problem changes. Recommended order:
progress metric → contract inversion → prompt reframe → harder problems.

## What to build next (not in MVP scope)

- **The Metaoptimizer agent** (SPEC §20): it consumes `runs/**` (all `compile.json`, failed
  prompts/outputs, `failure_report.md`, `state.json`) and writes suggestions to
  `EXTERNAL_AGENT_SUGGESTIONS.md`. The loop already logs everything it needs.
- **A progress checker** based on goal extraction at `sorry` positions (SPEC §15 future work).

The warm verifier backend is already integrated. Keep its Hard Mode contract aligned with the fresh
backend by running `python3 tests/test_hardmode_backend_parity.py` whenever a built warm executable
and Mathlib `LEAN_PATH` are available; see `INTEGRATION.md`.

## Where to look first
- `runs/p2_lemma/` — the fake/useless-progress failure, step by step (`attempts p2_lemma`).
- `PHASE4_REPORT.md` — the full run results.
- `LEMMA_FIRST_ANALYSIS.md` — the 5 causes + fixes.
- `NOTES.md` — every ambiguity/decision/finding from the build.
