## Output structure

- Suggest ONE proof step along with its proof.
- Avoid large jumps or too complex ideas, as this step will be later verified by a Lean 4 model.

- Do not propose a complete proof unless the theorem is clearly almost finished.
- Output should have an intermediate reasoning part, a NEXT STEP part, a PROOF part, a
  mandatory final-step signal required by the mode-specific action format, and IDEAS FOR THE
  FUTURE as the final section.

- For IS_FINAL_STEP, use exactly `True` or `False`.
- Use `True` only when this action completes the entire problem. Otherwise use `False`.


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
- End with a non-empty IDEAS FOR THE FUTURE roadmap. Prefer a High, Medium, or Low confidence label
  for each idea. Reassess the supplied roadmap: carry forward promising unfinished ideas and revise
  or discard stale ones. These ideas are planning context, not established facts.

## When faced with a geometry problem
- If it's a difficult geometry problem, focus on operative, algebraic solutions. Choose a mathematical representation, construct a substantial layer of configuration facts, and only then reduce the problem to a form that algebraic tactics can finish.
