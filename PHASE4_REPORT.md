# Phase 4 — full-run report (2026-07-09)

Live runs of all three problems (gpt-5 reasoning + claude-sonnet-5 translation via OpenRouter),
fresh `lean` per checkpoint, Core/Std only. Run artifacts under `runs/<pid>/` (gitignored).

## Phase 1 verifier gate (prerequisite)
`verifier/run_phase1.py` still PASSES: 3 good checkpoints + the required negative (unknown
identifier → declaration_check FAIL, body_check skipped, structured `error(lean.unknownIdentifier)`,
no leak). This gates everything below.

## Run status

| problem | status | wall | LLM calls | compiles | attempts | accepted steps |
|---|---|--:|--:|--:|--:|--:|
| p1_sanity | ✅ final_success | 15 s | 4 | 7 | 2 | 2 |
| p2_lemma | ❌ failed (`max_proof_steps`) | 251 s | 18 | 20 | 9 | 8 |
| p3_imo | ✅ final_success | 510 s | 27 | 37 | 17 | 9 |
| **total** | 2/3 solved | **776 s** | **49** | **64** | **28** | — |

**No crashes / unhandled exceptions** on any run (the only "bad" outcome, avoided).

## Top-priority finding — OutputParser on real output: CLEAN
Across **28 real translation attempts**, attempt-status distribution was:
`accepted ×17, body_failed ×9, final_success ×2` — **zero `parse_error`, zero `format_failed`.**
Every real gpt-5/claude output parsed into `NEW DECLARATIONS` / `UPDATED THEOREM BODY` and passed
all structure checks (§8/§12). The parser and structure checker held on genuine model output.

## Headline finding — the lemma-delta path was never used
**0 declarations across all 19 accepted steps in all 3 problems.** Both models proved (or tried to
prove) everything directly in the theorem body; every `declarations.lean` was an empty
comment ("-- none needed for this step"). Consequences:
- **p3_imo succeeded WITHOUT the intended lemma decomposition.** It used a body-only proof:
  `refine congrArg f ?_; induction n …` — reducing `f (dblA n) = f (dblB n)` to `dblA n = dblB n`
  and inducting directly. The problem was *designed* to need separate A/B lemmas; the model found a
  direct route and dodged them. (Solution compiles independently, no sorry — genuinely correct.)
- **p2_lemma failed for the opposite reason.** Its goal has NO body-only route: direct induction on
  `main` gives a too-weak IH (`sumAcc n 0 = sumTo n`, but the succ case needs `sumAcc n (0+(n+1))`).
  The correct move is to prove the GENERALIZED lemma `sumAcc n a = sumTo n + a` and specialize — a
  `NEW DECLARATIONS` lemma. The model never did this.

So the "lemma-first" premise is **unexercised by real models** on these problems: they avoid
declarations unless forced, and neither problem forced it hard enough (p3 had an escape hatch; p2
had none but the model still wouldn't switch strategies).

## §15 fake/useless-progress weakness — observed, as predicted
p2's 8 "accepted" steps were the same theorem body with **one more tactic line appended each time,
all ending in `sorry`** — flailing in the succ branch (successive `simp` / `rewrite [Nat.add_comm …]`
that don't converge), never closing and never stepping back to a lemma:
```
ps002  succ => simp [...]; sorry
ps003  succ => simp [...]; simp [...]; sorry
ps005  succ => simp; rewrite [Nat.add_comm (n+1) (sumTo n)]; sorry
ps008  succ => simp; rewrite ...; rewrite [← ih]; rewrite ...; rewrite ...; sorry
```
Each compiled with `sorry` allowed and *changed* the body, so §15's acceptance rule (no progress
metric) accepted it. The run correctly terminated at `max_proof_steps` (LimitController working) —
it did not loop forever. This is exactly the known weakness §15 flags; here it's real and observed.

## §12 unsound-command scan — clean
No `axiom` / `native_decide` / `unsafe` / `admit` in any accepted declaration across all runs. The
only `sorry`s were in intermediate accepted bodies (expected, not a violation).

## Retry logic — works on real output
Many genuine `body_failed` recovered via compiler feedback (p3: `ps004` took 5 attempts across 2
candidates before accepting; `ps009` took 3 to reach final_success). The compiler-feedback retry
loop is effective on real errors.

## Recommendations (for the metaoptimizer / next iteration — NOT auto-applied per scope)
1. **Add a progress metric (§15 future work).** p2 proves accepted-without-progress is real and
   silently wastes the whole step budget. Even a coarse check (does the goal at the `sorry` change?
   does the sorry count drop?) would have caught p2's spin.
2. **Reasoning prompt incentive for generalization.** Add to `INFORMAL_REASONING_CONSIDERATIONS.md`:
   "If a direct induction's hypothesis is too weak, prove a *generalized* lemma and specialize it."
   p2 needed exactly this and never tried it.
3. **Lemma-path pressure.** The models avoid `NEW DECLARATIONS`. To actually test the declaration
   delta, either (a) strengthen the translation prompt to prefer factoring reusable facts into
   lemmas, or (b) design problems with no body-only escape (p3 had `congrArg`; close that door).

## Totals
Wall 776 s (~13 min) · 49 LLM calls · 64 lean compiles · 28 translation attempts · 2/3 solved.
