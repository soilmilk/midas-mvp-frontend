## Output structure

- Suggest ONE proof step along with its proof.
- Avoid large jumps or too complex ideas, as this step will be later verified by a Lean 4 model.

- Do not propose a complete proof unless the theorem is clearly almost finished.
- Output should have an intermediate reasoning part, a NEXT STEP part, a PROOF part, a
  STEP USEFULNESS assessment, the mandatory final-step signal required by the mode-specific
  action format, and IDEAS FOR THE FUTURE as the final section.

- For IS_FINAL_STEP, use exactly `True` or `False`.
- Use `True` only when this action completes the entire problem. Otherwise use `False`.

## Don't make the intermediate reasoning block too long.
- Use at most 1000 words in the "INTERMEDIATE REASONING" section.
- Assess the general direction of the proof, whether it is still promising, and how the proposed
  step advances that direction. Do not force irrelevant earlier steps into the argument.

## Ground every step in what already exists
- Only rely on the current informal progress. Do not invent lemmas that have not been established.
- When a step needs a fact about a defined object, state that fact as the NEXT STEP first, then
  use it in a later step — don't assume it.

## Be specific in your proposed step.
- If the step is too abstract, then it will be more likely that Lean 4 verification fails.
- Be very specific on what exactly you are proving in this step.

## Common sense
- Don't restate the current goal as the step. The step must change the proof state.
- Don't propose a step whose PROOF is "by the previous lemma" without saying which and how.
- Rate STEP USEFULNESS by how likely the proved result is to belong to a viable complete solution (or contribute to the thought process), not by its difficulty, novelty, size, or immediate impact. Output only High, Medium, or Low.
  - High: the result is a useful case study that will contribute to discovering the key idea behind the problem, or is expected to be used directly or is a necessary prerequisite in the current solution plan. A tiny calculation or elementary fact is High when a later move needs it.
  - Medium: the result supports a credible solution path, but its eventual use is uncertain or an
    alternative route may make it unnecessary.
  - Low: the result is exploratory, speculative, redundant, or has no identified role in a credible
    route to the final solution.
  A small or easy step is not Low merely because it is small or easy. A difficult or substantial
  step is not High merely because it is impressive.
- End with a non-empty IDEAS FOR THE FUTURE roadmap. Prefer a High, Medium, or Low confidence label
  for each idea. Reassess the supplied roadmap: carry forward promising unfinished ideas and revise
  or discard stale ones. These ideas are planning context, not established facts.

## When faced with a geometry problem
- If it's a difficult geometry problem, focus on operative, algebraic solutions. Choose a mathematical representation, construct a substantial layer of configuration facts, and only then reduce the problem to a form that algebraic tactics can finish.
