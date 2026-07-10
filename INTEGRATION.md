# INTEGRATION — midas-mvp ↔ midas_proof_verifier

The two repos are the two halves of one system:

```
  midas-mvp                     the ORCHESTRATION loop
  (reason → translate →         (this repo)
   parse → structure-check →
   VERIFY → accept/retry →           │  VerifierClient (SPEC §14)
   reconstruct)                      │  declaration check (no sorry) → body check (sorry ok)
                                     ▼
  ┌──────────────────────────────────────────────────────────────┐
  │  VerifierBackend  (midas/verifier_client.py)  ← the link seam │
  ├───────────────────────────┬──────────────────────────────────┤
  │  FreshCompileBackend       │  WarmTxnBackend                   │
  │  (default)                 │  (opt-in)                         │
  │  fresh `lean` per check    │  → midas_proof_verifier `warm`    │
  │  Core/Std: ~0.3–0.6 s      │  Mathlib resident, paid ONCE      │
  └───────────────────────────┴──────────────────────────────────┘
                                     │
                                     ▼
  midas_proof_verifier          the warm VERIFIER backend
  (Warm/WarmChain/WarmTxn)      github.com/soilmilk/midas_proof_verifier
```

Both backends implement the same `VerifierBackend` interface and return the same `CheckpointResult`,
so **the loop never changes** — only `config.verifier_backend` does.

## Why link them
The checkpoint semantics midas-mvp needs (SPEC §14: declaration check *no sorry* → body check
*sorry allowed*, in accumulated context) are exactly what `midas_proof_verifier`'s warm server
implements. midas-mvp defaults to a fresh `lean` per checkpoint — great for Core/Std, where a cold
compile is sub-second. But with a **Mathlib prelude**, a cold compile is ~15–40 s *every checkpoint*.
The warm server pays `import Mathlib` once and verifies each block in ~10–500 ms. Measured break-even
is ≈ **2 checkpoints** — so for any real (Mathlib) proof, the warm backend is the one you want.

## When to use which
| prelude | backend | why |
|---|---|---|
| `[]` (Core/Std) — the current problems | `fresh` (default) | cold compile is ~0.3–0.6 s; no server worth the complexity |
| `["import Mathlib", …]` | `warm` | fresh re-pays ~15–40 s/checkpoint; warm amortizes it to one load |

## How to enable the warm backend
1. Build the warm exe in `midas_proof_verifier` (`cd warm-server && lake build warm`) and get a
   Mathlib `LEAN_PATH` (`lake env printenv LEAN_PATH` from a v4.31.0 Mathlib project).
2. In the problem's `config.json`:
   ```json
   {
     "lean_prelude": ["import Mathlib", "set_option maxHeartbeats 0"],
     "verifier_backend": "warm",
     "warm_binary": "/path/to/midas_proof_verifier/warm-server/.lake/build/bin/warm",
     "warm_lean_path": "/path/to/mathlib/.lake/build/lib/lean:..."
   }
   ```
   (or set `$MIDAS_WARM_BINARY` / `$MIDAS_WARM_LEAN_PATH`).
3. Run as usual: `python3 -m midas.cli run problems/<mathlib_problem>`.

## Status — VALIDATED (2026-07-09)
`WarmTxnBackend` verdicts were diffed against `FreshCompileBackend` on shared Mathlib cases (sorry
body / partial proof / broken body) — identical. First real use: **p4_n5_30 (`30 ∣ n⁵−n`) reached
`final_success` on the warm backend in 770 s (13 steps), where the `fresh` backend timed out at 12
steps in 1240 s** — the difference was per-checkpoint `import Mathlib` (~36 s × 34) vs one ~7 s load.
Original experimental notes kept below for reference.

### Original notes
`midas/warm_backend.py` is the documented adapter but is **unvalidated**: the MVP problems are
Core/Std, so nothing here has exercised a Mathlib prelude end-to-end. The MVP build scope
deliberately kept the warm backend out; this adds the *seam* and a starting-point adapter, not a
validated path. Before relying on it:
- Run a Mathlib-prelude problem and diff `warm` vs `fresh` verdicts on a shared core case.
- Harden the response protocol: have `warm` print a per-response sentinel (e.g. `%%DONE`) so
  `_submit()` doesn't rely on scanning for the next `[node …]` line (see the file header).
- Note: `WarmTxnBackend` uses the *stateless* `warm` exe (each block gated independently against
  resident Mathlib), which matches midas-mvp's stateless `check()`. `warmtxn` (with commit/rollback)
  is a different protocol; only adopt it if you also make midas-mvp's loop drive the checkpoint
  session rather than pass `accepted_declarations` forward itself.

## Reverse pointer
`midas_proof_verifier` is the verification engine; midas-mvp is its first orchestration consumer.
See that repo's README (§ "Used as a backend by midas-mvp") and `docs/TRANSACTIONAL.md`.
