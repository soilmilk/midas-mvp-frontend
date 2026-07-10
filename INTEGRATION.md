# INTEGRATION — midas-mvp warm verifier

`midas-mvp` has two verifier backends behind the same `VerifierBackend` interface:

```
FreshCompileBackend       default; starts a fresh `lean` process per checkpoint
WarmTxnBackend            opt-in; talks to `warm-server/.lake/build/bin/warm`
```

Both return the same `CheckpointResult`, so the proof-search loop does not change. Select the backend
with `config.verifier_backend` (`"fresh"` or `"warm"`).

## Why use warm
The checkpoint semantics midas-mvp needs are fixed: declaration check with no `sorry`, then body check
with `sorry` allowed, both in accumulated context. The fresh backend implements that by invoking
`lean` for every checkpoint. That is fine for Core/Std problems, but a Mathlib prelude repays
`import Mathlib` on every checkpoint.

The warm backend starts one long-lived Lean process, loads Mathlib once from `LEAN_PATH`, and sends
each checkpoint block over stdin. The current adapter uses the stateless `warm` executable: midas-mvp
passes the accepted declarations on every call, and `warm` verifies each submitted block against the
resident Mathlib environment.

## When to use which

| prelude | backend | why |
|---|---|---|
| `[]` or Core/Std-only | `fresh` | no server setup; cold compile is cheap |
| `["import Mathlib", ...]` | `warm` | avoids reloading Mathlib for every checkpoint |

## How to enable the warm backend

1. Build the in-repo warm executable:
   ```bash
   cd warm-server
   lake build warm
   cd ..
   ```

2. Provide a Mathlib `LEAN_PATH` from a prebuilt v4.31.0 Mathlib host project:
   ```bash
   export MIDAS_WARM_LEAN_PATH="$(cd ~/mathlib_host && lake env printenv LEAN_PATH)"
   ```

   Equivalent options are supported, in precedence order: `config.warm_lean_path`,
   `$MIDAS_WARM_LEAN_PATH`, `$LEAN_PATH`, or `warm-server/mathlib_leanpath.txt`.

3. In the problem config, set the backend:
   ```json
   {
     "lean_prelude": ["import Mathlib", "set_option maxHeartbeats 0"],
     "verifier_backend": "warm"
   }
   ```

   `warm_binary` is optional. If omitted, midas-mvp uses
   `warm-server/.lake/build/bin/warm`. You can still override it with `config.warm_binary` or
   `$MIDAS_WARM_BINARY`.

4. Run as usual:
   ```bash
   python3 -m midas.cli run problems/<mathlib_problem>
   ```

## Status — VALIDATED (2026-07-09)

`WarmTxnBackend` verdicts were diffed against `FreshCompileBackend` on shared Mathlib cases: sorry
body, partial proof, and broken body. Verdicts matched. First real use: `p4_n5_30` (`30 ∣ n⁵−n`)
reached `final_success` on the warm backend in 770 s (13 steps), while the fresh backend timed out at
12 steps in 1240 s. The main difference was per-checkpoint `import Mathlib` versus one warm load.

## Notes

- The warm executable builds against Lean compiler APIs only. Mathlib is loaded at runtime through
  `LEAN_PATH`, so the Mathlib host project remains machine-local and is not committed here.
- `WarmTxnBackend` currently uses the stateless `warm` executable. The vendored `warmtxn` executable
  has a transactional commit/rollback protocol, but adopting it would require changing midas-mvp to
  drive a stateful verifier session instead of passing `accepted_declarations` into every check.
- Optional hardening remains: have `warm` print a per-response sentinel, such as `%%DONE`, so
  `_submit()` does not rely on scanning for the next `[node ...]` line.
