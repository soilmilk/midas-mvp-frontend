# NOTES — SPEC.md review, ambiguities, and judgment calls

First-pass review of SPEC.md before building. Flag-and-proceed; where a call was made to keep
moving it is recorded with the reason.

## Blockers (outside code, need resolution before Phase 2/4)
- **API keys not set.** `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` are absent in this environment.
  Phase 1 (verifier) needs neither and is done. Phase 2 agents and Phase 4 runs cannot execute
  until they're set.
- **Model IDs unspecified.** "GPT5" (§9) and "Claude/GLM" (§10) have no exact model id in the spec.
  Decision: put `reasoning_model` and `translation_model` in `config.json`, defaulted, overridable.

## Missing / referenced-but-absent files
- **`INFORMAL_REASONING_CONSIDERATIONS.md` (§9)** has no source document anywhere — only
  `LEAN4_TRANSLATOR_CONSIDERATIONS.md` and `METAOPTIMIZING.md` exist. Decision: author
  `INFORMAL_REASONING_CONSIDERATIONS.md` fresh from §9's stated role (small steps, avoid large
  jumps, one candidate).
- **`FORMAL_TRANSLATION_CONSIDERATIONS.md` (§10)**: adapt from the existing
  `LEAN4_TRANSLATOR_CONSIDERATIONS.md` (same purpose — the translator's Considerations block).

## Spec ambiguities / internal tensions
1. **Config limits differ across the doc.** §2 example uses `max_proof_steps: 40`,
   `max_total_lean_attempts: 300`, `max_runtime_seconds: 3600`. The Phase 3 toy problems want small
   fast limits. Decision: per-problem `config.json`; toy problems use small caps (e.g. steps 6–10),
   the §2 numbers are the reference default.
2. **Theorem-header extraction rule is implied, not stated.** §5/§8 store the header as the string
   up to and including `:= by`. Decision: header = substring of `body_initial.lean` from the first
   `theorem` through the first `:= by` (inclusive), stored verbatim with newlines. Every later
   `body.lean` must start byte-for-byte with this. (Term-mode `:=` targets are not handled — the
   spec's examples are all `:= by`.)
3. **`format_failed` conflates two failure sources.** §11 (parse: a required section missing) and
   §12 (structure check: sorry-in-decl, header changed, repeated names, >1 theorem) both map to
   `format_failed` (§17). Decision: keep one status `format_failed`; record which sub-check tripped
   inside `compile.json.structure_check.errors` so the metaoptimizer can still distinguish them.
4. **Declarations may be empty (§7) — declaration check on empty delta.** Then the declaration check
   is just `prelude + context + accepted`, which already compiled at the previous checkpoint.
   Decision: run it anyway (cheap, and it re-confirms accumulated state); empty candidate ⇒ pass
   unless accumulated state itself regressed.
5. **"no sorry" enforcement is textual+compiler, not axiom-level.** §14 declaration check says "no
   sorry"; §12 also bans `sorry` in declarations. Decision (MVP): treat declaration check as failed
   if the compiler emits the `` uses `sorry` `` warning OR the text contains `sorry`. §12's known
   weakness (no ban on `axiom`/`unsafe`/`native_decide`) is left unenforced per spec, but the loop
   logs enough for the metaoptimizer to catch it, and Phase 4 reporting will flag any such case.
6. **`nearby_code` (§19) source is unspecified.** Decision: populate it with the offending source
   line (from the assembled temp file) when available; otherwise empty string.
7. **`proof_step.status` has both `failed` (§17) and the run-level `failed`.** A proof_step fails
   only after all 3 candidates are abandoned (§16); that failure immediately fails the run (§18).
   Decision: model both; a failed proof_step is terminal for the run.
8. **Retry feedback content.** §9/§10 say "latest failure feedback / compiler feedback" without a
   format. Decision: pass the previous attempt's `compile.json` errors (structured) plus the raw
   compiler output, and for informal reprompt after 3 failed translations, §9's fixed sentence.

## Phase 2 — build + live-run findings (2026-07-09)

Key handling: OpenRouter key stored at `~/.midas-mvp.env` (chmod 600) **outside the repo tree**;
`.gitignore` blocks `.env`/`*.env`; a `sk-or-` leak scan runs before every push (clean).

Live smoke run of `problems/toy` (gpt-5 + claude-sonnet-5 via OpenRouter) → **final_success** in
~40 s, 5 LLM calls, 3 lean attempts, 9 compiles. Findings:
1. **gpt-5 is a reasoning model** — needs a large `max_tokens` (small budgets return EMPTY content;
   confirmed 20 tokens→'', 3000→'pong' w/ 64 reasoning tokens). Reasoning agent set to 16000.
2. **Latency / hang risk.** The first live attempt ran >2 min before I killed it; the retry finished
   in 40 s. Added `timeout=180s, max_retries=2` to the client so a hung upstream call can't stall the
   loop. Still: a full multi-step Mathlib run could be slow; watch `max_runtime_seconds`.
3. **Real `body_failed` caught + recovered.** Model emitted `simp only [A,B]` (which *closed* the
   goal) then a stray `sorry` → compiler `"No goals to be solved"` → body_failed; retry with
   compiler feedback produced `dsimp` and succeeded. The retry-with-feedback path works on real output.
4. **Parser survived real output** — no `format_failed` on genuine model output in this run (the
   top-priority surface held; Phase 4 on harder problems will test it more).
5. **Toy under-exercises the lemma path.** Both accepted steps had *empty* NEW DECLARATIONS (the model
   proved directly in the body), so the declaration-delta / lemma-first path wasn't hit live. Phase 3
   problems 2–3 (genuine reusable lemmas) are needed to exercise it. Also: comment-only "empty" deltas
   get appended verbatim in reconstruction (harmless duplicate comments) — candidate cleanup later.
6. §12 unenforced weakness (axiom/unsafe/native_decide) — none appeared; the only `sorry` is in an
   intermediate accepted body (`ps001`), which is expected, not a violation. Phase 4 report will scan.

## Phase 1 judgment calls (implemented)
- Toy `context.lean` uses no imports (`prelude=[]`), so every compile is a fresh core-only `lean`
  (~0.3–0.6 s). `A = 2+3`, `B = 15/3` (both 5, structurally different), `f n = n*n`; target
  `f A = f B`, proved via `A_eq`, `B_eq`, then `key`.
- Negative fixture uses an unknown identifier → `error(lean.unknownIdentifier)`, which exercises the
  `(code)` parse path directly.
- "No leak" is structural in the fresh-compile backend: `build_checkpoint` assembles only from the
  `accepted_declarations` passed in, so a rejected candidate simply is never appended. The Phase 1
  test asserts `accepted` is unchanged after the negative checkpoint.
