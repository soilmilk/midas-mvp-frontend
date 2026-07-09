# PENDING_PATTERNS

Patterns seen in runs but **not yet promoted to a Considerations rule** — because they haven't hit
the 3+-in-a-batch threshold, weren't fatal, or aren't fixable at the prompt level (METAOPTIMIZING.md
§3). Promote to `EXTERNAL_AGENT_SUGGESTIONS.md` once a pattern recurs or turns fatal.

| id | pattern | seen in | count | why still pending |
|---|---|---|---|---|
| P1 | **body-only instead of lemma-first** — step proves directly in the theorem body, `NEW DECLARATIONS` empty | p1, p2, p3 (all) | 19/19 accepted steps | Usually *succeeds*, so it's not a failure the taxonomy catches. **Not a prompt-level fix** — needs a structural change (require declarations on non-final steps + progress metric). See `LEMMA_FIRST_ANALYSIS.md`. |
| P2 | **`congrArg`/structural collapse of an intended multi-lemma goal** | p3_imo | 1 | Single occurrence; it's really a *problem-design* leak (a body-only escape existed), not a model rule. Fix the problem, not the prompt. |
| P3 | **stray `sorry` after a goal is already closed** (`simp` closed it, then `sorry` → "No goals to be solved") | toy (Phase 2 live) | 1 | Single occurrence; the retry loop recovered. Watch for recurrence; if 3+, add a translator rule "don't leave a trailing `sorry` after a closing tactic." |

## Notes
- Promotion rule: a pattern moves to `EXTERNAL_AGENT_SUGGESTIONS.md` at **3+ occurrences in one
  batch** or on any **fatal failure**. P1 is pervasive but structural, so it's tracked here and in
  `LEMMA_FIRST_ANALYSIS.md` rather than turned into a (futile) prompt rule.
- Keep counts per batch; reset the batch when you bump `considerations_version`.
