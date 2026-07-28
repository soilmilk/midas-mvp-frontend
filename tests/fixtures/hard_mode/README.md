# Hard Mode Phase 0 fixtures

These Core-only snippets establish the source shapes and expected Lean verdicts
for Hard Mode development. They are test data, not a complete problem directory;
production Hard Mode loading and verification begin in Phase 1.

The authoritative source order is:

```text
context
accepted and candidate declarations
placeholder
main theorem
```

Fixture expectations:

| Fixture | Placement | Expected result |
|---|---|---|
| `context.lean` | Stable context | Compiles |
| `placeholder.lean` | Unresolved placeholder | Compiles with a `sorry` warning |
| `body_initial.lean` | Initial theorem after the placeholder | Compiles with a `sorry` warning |
| `valid_intermediate_declaration.lean` | Before the placeholder | Compiles without `sorry` |
| `invalid_declaration_refers_to_placeholder.lean` | Before the placeholder | Fails because `answer` is not yet in scope |
| `valid_filled_placeholder.lean` | Final placeholder replacement | Compiles in the complete final source |
| `invalid_filled_placeholder.lean` | Final placeholder replacement | Fails with a type mismatch |
| `valid_final_theorem.lean` | After the filled placeholder | Compiles without `sorry` |
| `invalid_final_theorem.lean` | After the filled placeholder | Fails with a type mismatch |
| `optional_final_declaration.lean` | Before the filled placeholder | Compiles and may be included in the final transaction |

The valid final transaction is:

```text
context.lean
optional_final_declaration.lean
valid_filled_placeholder.lean
valid_final_theorem.lean
```

No snippet relies on Mathlib, so fixture checks require only the repository's
configured Lean toolchain.
