# EXTERNAL_AGENT_SUGGESTIONS

Proposed prompt patches for the `considerations/` files, with motivation (SPEC §20). The
Metaoptimizer agent writes here; a developer applies edits manually. This file is **seeded from the
Phase-4 manual review** (2026-07-09) — treat these as the first batch of suggestions, not yet applied.

Format per METAOPTIMIZING.md §4: one rule per entry, routed to a prompt, with the batch/evidence and
a proposed `considerations_version` bump.

---

## S1 — reasoner: generalize when the induction hypothesis is too weak
- **Route:** `INFORMAL_REASONING_CONSIDERATIONS.md`  →  bump to v0.2
- **Evidence:** `p2_lemma` (fatal — `max_proof_steps`). The goal needs the generalized lemma
  `sumAcc n a = sumTo n + a`; direct induction on `main` gives a too-weak IH. The reasoner never
  proposed a generalization — it kept extending the body's `succ` branch.
- **Proposed rule:** "If a direct induction's hypothesis is too weak to close the successor case,
  do **not** keep adding tactics — propose a *stronger, generalized lemma* (extra parameter /
  universally quantified accumulator) as the next step, then specialize it."

## S2 — translator: prefer a named lemma over growing the body
- **Route:** `FORMAL_TRANSLATION_CONSIDERATIONS.md`  →  bump to v0.2
- **Evidence:** 0/19 accepted steps across all Phase-4 runs used `NEW DECLARATIONS`; models proved
  directly in the body every time. The "Structure-only steps → leave NEW DECLARATIONS empty" rule is
  being over-applied.
- **Proposed rule:** "When the step establishes a reusable fact (anything more than a one-line
  structural move), put it in `NEW DECLARATIONS` as a named lemma and have the body *cite* it; do
  not inline multi-step reasoning into the theorem body."
- **Note:** this is a nudge only. It will not fully fix the skip — see `LEMMA_FIRST_ANALYSIS.md`;
  the structural fix (require declarations on non-final steps + a progress metric) is needed too.

## S3 — reasoner: don't append to a non-converging proof
- **Route:** `INFORMAL_REASONING_CONSIDERATIONS.md`  →  v0.2
- **Evidence:** `p2_lemma` steps ps002–ps008 each appended one more tactic to the same failing
  branch, all ending in `sorry`, never converging (live instance of §15 fake/useless progress).
- **Proposed rule:** "If the previous step did not reduce the remaining goal, do not extend the same
  tactic block. Change strategy — a different lemma, a generalization, or a different case split."

---

## Pending (not yet 3+ occurrences or not fatal) — see PENDING_PATTERNS.md
- The body-only-instead-of-lemma pattern is pervasive but usually *succeeds*, so the failure
  taxonomy doesn't catch it. Logged as a structural issue, not a promotable prompt rule.
