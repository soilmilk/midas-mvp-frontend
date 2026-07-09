# Informal Reasoning Considerations (v0.1)

Inserted into the informal-reasoning prompt (SPEC §9). Seeded from the spec's stated role;
expected to grow as the metaoptimizer observes failure patterns. Every rule is a prior, not
yet an observed pattern.

## Output shape (hard requirement)
- Output exactly: optional `[intermediate reasoning]`, then `NEXT STEP:`, then `PROOF:`.
- `NEXT STEP` is ONE small, self-contained advance — a single lemma or a single tactic-level
  move — not a multi-part plan.
- `PROOF` is the informal justification of that one step, short enough that a translator can
  render it in a few Lean lines.

## Keep steps small and translatable
- Prefer a step that introduces one named intermediate fact about one object over a step that
  combines several. The loop will combine them later.
- Do not propose a complete proof unless the current theorem body is clearly one obvious move
  from done.
- If asked to simplify after a failed candidate, propose a *strictly smaller* step: fewer
  hypotheses used, a more elementary tactic, or splitting the previous step in two.

## Ground every step in what already exists
- Only rely on definitions in the fixed context and on already-accepted declarations (they are
  listed for you). Do not invent lemmas that have not been established.
- When a step needs a fact about a defined object, state that fact as the NEXT STEP first, then
  use it in a later step — don't assume it.

## Avoid dead ends
- Don't restate the current goal as the step. The step must change the proof state.
- Don't propose a step whose PROOF is "by the previous lemma" without saying which and how.
