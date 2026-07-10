/- Persistent warm-environment VERIFIER (paste-friendly, hardened).
   import once, then read `%%`-terminated blocks and verify each:
     1. elaborate                    -> reject on any compile error
     2. `#print axioms <name>` for EVERY declared theorem/lemma in the block
        -> reject sorryAx, reject if the probe itself errors, FLAG non-standard axioms.
   A `set_option maxHeartbeats N` (2nd CLI arg) makes runaway ELABORATION fail
   deterministically instead of hanging (does not cover native_decide/kernel). -/
import Lean.Elab.Frontend
open Lean Elab

def runNew (base : Command.State) (input : String) : IO (Command.State × List Message) := do
  let inputCtx := Parser.mkInputContext input "<req>"
  let base := { base with messages := {}, infoState.enabled := true }
  let s ← IO.processCommands inputCtx {} base <&> Frontend.State.commandState
  pure (s, s.messages.toList)

def loadEnv (input : String) : IO Command.State := unsafe do
  let inputCtx := Parser.mkInputContext input "<load>"
  let (header, parserState, messages) ← Parser.parseHeader inputCtx
  let (env, messages) ← processHeader header {} messages inputCtx
  let s ← IO.processCommands inputCtx parserState
            { (Command.mkState env messages {}) with infoState.enabled := true }
            <&> Frontend.State.commandState
  pure s

def hasError (msgs : List Message) : Bool := msgs.any (·.severity == MessageSeverity.error)
def allText (msgs : List Message) : IO String := do
  let mut s := ""
  for m in msgs do s := s ++ (← m.toString)
  return s
def contains (hay needle : String) : Bool := (hay.splitOn needle).length > 1
def isStd (n : String) : Bool := n == "propext" || n == "Classical.choice" || n == "Quot.sound"

def axiomVerdict (axtxt : String) : String :=
  if contains axtxt "sorryAx" then "sorry"
  else if contains axtxt "does not depend" then "clean"
  else
    let inside := (((axtxt.splitOn "[").getD 1 "").splitOn "]").headD ""
    let names := (inside.splitOn ",").map (·.trim) |>.filter (· ≠ "")
    let bad := names.filter (fun n => ! isStd n)
    if bad.isEmpty then "clean" else "nonstd: " ++ ", ".intercalate bad

/-- identifier incl. dots, e.g. `My.lemma_x`. -/
def identOf (s : String) : String := (s.takeWhile fun c => c.isAlphanum || c == '_' || c == '.').toString

partial def collectNames : List String → List String → List String
  | "theorem" :: n :: rest, acc => collectNames rest (identOf n :: acc)
  | "lemma"   :: n :: rest, acc => collectNames rest (identOf n :: acc)
  | _ :: rest, acc => collectNames rest acc
  | [], acc => acc.reverse

/-- every declared theorem/lemma name in the block (dotted-safe). -/
def allNames (input : String) : List String :=
  let toks := ((input.splitOn " ").flatMap (·.splitOn "\n")).flatMap (·.splitOn "\t") |>.filter (· ≠ "")
  collectNames toks []

def oneLine (s : String) : String := ((s.replace "\n" " ").take 90).toString

def leadWs (s : String) : String := (s.takeWhile fun c => c == ' ' || c == '\t').toString

/-- insert `trace_state` on the line before every bare `sorry`. -/
def injectTraceState (src : String) : String :=
  let lines := src.splitOn "\n"
  "\n".intercalate <| lines.flatMap fun ln =>
    if ln.trim == "sorry" then [leadWs ln ++ "trace_state", ln] else [ln]

/-- goal-state text at each sorry: re-elaborate with trace_state, keep ⊢-messages. -/
unsafe def extractGoals (base : Command.State) (src : String) : IO (List String) := do
  let (_, msgs) ← runNew base (injectTraceState src)
  let mut gs : List String := []
  for m in msgs do
    if m.severity != MessageSeverity.error then
      let t ← m.toString
      if contains t "⊢" then gs := gs ++ [t]
  return gs

/-- drop `import …` lines: Mathlib is already resident in `base`. -/
def stripImports (src : String) : String :=
  "\n".intercalate <| (src.splitOn "\n").filter fun ln => ! ("import ".isPrefixOf ln.trim)

unsafe def verify (base : Command.State) (src0 : String) : IO String := do
  let src := stripImports src0
  let (st, msgs) ← runNew base src
  let txt ← allText msgs
  if hasError msgs then
    return s!"REJECT  (compile error)   :: {oneLine txt}"
  let names := allNames src
  if names.isEmpty then
    if contains txt "sorry" then
      let gs ← extractGoals base src
      return s!"OPEN  (unnamed, {gs.length} hole(s))\n" ++ "\n─── goal ───\n".intercalate gs
    else return "ACCEPT  (no named theorem — not axiom-gated)"
  let mut flagged : List String := []
  let mut sorried : List String := []
  for nm in names do
    let (_, axmsgs) ← runNew st s!"#print axioms {nm}"
    if hasError axmsgs then                      -- FIX 1: never swallow a probe error
      return s!"REJECT  (axiom probe failed for '{nm}')   :: {oneLine (← allText axmsgs)}"
    let av := axiomVerdict (← allText axmsgs)
    if av == "sorry" then sorried := sorried ++ [nm]
    else if contains av "nonstd:" then flagged := flagged ++ [s!"{nm} → {av}"]
  if ! flagged.isEmpty then                       -- FIX 2: any decl can trip the flag
    return s!"⚠ FLAG   (non-standard axiom)   :: {"; ".intercalate flagged}"
  else if ! sorried.isEmpty then                  -- body-style: report goals, don't reject
    let gs ← extractGoals base src
    return s!"OPEN  (sorry in: {", ".intercalate sorried}) — {gs.length} goal(s):\n"
      ++ "\n─── next goal ───\n".intercalate gs
  else
    return s!"ACCEPT   :: {", ".intercalate names}"

unsafe def mainImpl (args : List String) : IO Unit := do
  Lean.initSearchPath (← Lean.findSysroot)
  enableInitializersExecution
  let lib := args.headD "Mathlib"
  let hb  := (args.drop 1).headD "200000"          -- FIX 3: maxHeartbeats
  let t0 ← IO.monoMsNow
  let base ← loadEnv s!"import {lib}\nset_option maxHeartbeats {hb}"
  let t1 ← IO.monoMsNow
  IO.println s!"[load] import {lib}: {t1 - t0} ms | modules={base.env.allImportedModuleNames.size} | maxHeartbeats={hb}"
  IO.println "[ready] paste a proof, then a line `%%` to submit (EOF also submits)."
  (← IO.getStdout).flush
  let stdin ← IO.getStdin
  let mut buf := ""
  let mut i := 0
  let submit := fun (b : String) (idx : Nat) => do
    let cmd := b.trim
    if cmd.isEmpty then return idx
    let a ← IO.monoMsNow
    let v ← verify base cmd
    let z ← IO.monoMsNow
    IO.println s!"[node {idx}] {z - a} ms  {v}"
    (← IO.getStdout).flush
    return idx + 1
  repeat
    let line ← stdin.getLine
    if line.isEmpty then
      i ← submit buf i; break
    else if line.trim == "%%" then
      i ← submit buf i; buf := ""
    else
      buf := buf ++ line

unsafe def main (args : List String) : IO Unit := mainImpl args
