## Output structure

- Suggest ONE proof step that should be translatable to Lean 4, along with its proof.
- Avoid large jumps. Prefer a step that introduces one named intermediate fact about one object over a step that
  combines several (the loop will combine them later). 

- Do not propose a complete proof unless the theorem is clearly almost finished.
- Output should have an intermediate reasoning part, a NEXT STEP part, and a PROOF part.

Follow this structure for the output:

INTERMEDIATE REASONING:
<intermediate reasoning - assess current progress, explore mathematical ideas, decide what the next step should be>

NEXT STEP:
<one step>

PROOF:
<detailed proof of that step>


## Ground every step in what already exists
- Only rely on the current informal progress. Do not invent lemmas that have not been established.
- When a step needs a fact about a defined object, state that fact as the NEXT STEP first, then
  use it in a later step — don't assume it.

## Common sense
- Don't restate the current goal as the step. The step must change the proof state.
- Don't propose a step whose PROOF is "by the previous lemma" without saying which and how.
