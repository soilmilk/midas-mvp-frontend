# Why the current setup skips the lemma-first form

Phase 4 evidence: **0 declarations across all 19 accepted steps in all 3 problems.** The models
never used `NEW DECLARATIONS`; they proved (or tried to prove) everything directly in `body.lean`.
"Lemma-first" is currently an *aspiration in the naming*, not something the mechanics require or
reward. Five concrete causes, each grounded in the code/spec and the run data.

## 1. The output contract *permits* empty declarations — so the model takes the easy path
`§7`, `§10`, and `§12` all say **"NEW DECLARATIONS may be empty."** `FORMAL_TRANSLATION_CONSIDERATIONS.md`
even has a "Structure-only steps → leave NEW DECLARATIONS empty" rule. The acceptance rule (`§15`)
accepts *any* body change that compiles with `sorry`. So a body-only proof is fully sanctioned and
costs the model nothing. There is no reward, and no requirement, to factor a lemma.
→ Evidence: `attempts <pid>` shows `declarations? empty` for every accepted step.

## 2. The reasoning prompt asks for a "step," not a "lemma"
`§9`: *"Suggest one proof step … along with its proof. Avoid large jumps."* The model reads "step"
as "the next tactic(s) to add to the body," not "a reusable named fact to establish." The framing
invites incremental body editing.
→ Evidence: p2's successive steps were literally *"append one more tactic line to the `succ` branch"*
(`simp` → `simp; rewrite` → `simp; rewrite; rewrite …`), never *"prove lemma X."*

## 3. `body.lean` is a mutable full-theorem artifact the model keeps rewriting
Because the body is the *complete theorem*, replaced wholesale each step, the natural loop is "keep
editing the proof script." Nothing pulls work *out* of the body into declarations. The architecture
itself is body-centric: one evolving body, an optional side-slot for lemmas.

## 4. No progress metric → flailing *counts* as progress, so the model never has to switch to lemmas
`§15` names this: there's no check that a step actually advanced. A body that adds a non-converging
tactic and still ends in `sorry` is "accepted." So the model can spend the entire step budget
editing the body without ever hitting a wall that would push it toward a generalized lemma.
→ Evidence: p2 — 8 "accepted" steps, none converging, `→ max_proof_steps`. The one problem that
*required* a lemma (a generalized IH) still didn't get one, because the loop kept green-lighting the
dead-end body edits instead of signalling "you're stuck, restructure."

## 5. Problem design left body-only escapes
Real models find the shortest route. p3_imo was *designed* to need separate A/B lemmas, but a
body-only proof existed (`refine congrArg f ?_; induction …`) and the model took it. Only a problem
with **no** body-only route forces the issue — and even then (p2) causes 1–4 dominate and it fails
rather than reaching for a lemma.

---

## The core tension
The system is "lemma-first" in name only. Every mechanism — the optional declarations slot, the
"step" framing, the mutable body, the no-progress-metric acceptance rule — **defaults to body-only**
and the models rationally follow the default. To make it lemma-first in fact, the defaults have to
invert.

## What would actually enforce it (fixes mapped to causes)
- **(cause 1,3) Invert the translation default.** For non-final steps, *require* a non-empty
  `NEW DECLARATIONS` (a named lemma) and restrict the body to citing accepted lemmas
  (`exact`/`apply`/`rw [lemma]`), not growing an ad-hoc tactic block. Make body-only the exception,
  not the default.
- **(cause 2) Reframe the reasoning prompt** from "suggest the next step" to "state the next *lemma*
  to prove and its proof sketch"; only allow a body-only step when the theorem is one citation from
  done.
- **(cause 4) Add a progress metric.** Reject a step whose goal-at-`sorry` / sorry-count didn't
  improve (goal extraction at the hole, as `§15` future-work suggests). This alone would have
  stopped p2's spin and forced a rethink toward the generalization.
- **(cause 5) Remove body-only escapes** in problems meant to test the path (e.g., phrase p3 so
  `congrArg` doesn't collapse it).
- **(reasoning content) Add the "generalize when the IH is too weak" rule** to
  `INFORMAL_REASONING_CONSIDERATIONS.md` — the exact move p2 needed.

These are the metaoptimizer's job (`§20`, out of MVP scope) — listed here as the concrete levers the
run data points to, not applied.
