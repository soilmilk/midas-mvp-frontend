/- Persistent warm-environment CHAINED verifier.
   Like Warm.lean, but the environment ACCUMULATES: every clean ACCEPT folds its
   declarations into the base env, so a later node only elaborates its NEW lemmas
   (milliseconds) instead of re-elaborating the whole chain. Bodies (OPEN / sorry)
   report their goal state but are NOT accumulated (they carry sorryAx).
   Feed nodes in dependency order: v1_lemma, v1_body, v2_lemma, v2_body, …  -/
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
  for m in msgs do s := s ++ (← m.toString) ++ "\n"
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

def wireText (s : String) : String := s.replace "\n" "␤"

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

/-- drop `import …` lines: Mathlib (and accumulated lemmas) already resident in `base`. -/
def stripImports (src : String) : String :=
  "\n".intercalate <| (src.splitOn "\n").filter fun ln => ! ("import ".isPrefixOf ln.trim)

/-- returns (verdict, accumulate?, post-elaboration state).
    accumulate? = true only for a clean ACCEPT (fold decls into the running env). -/
unsafe def verify (base : Command.State) (src0 : String) : IO (String × Bool × Command.State) := do
  let src := stripImports src0
  let (st, msgs) ← runNew base src
  let txt ← allText msgs
  if hasError msgs then
    return (s!"REJECT  (compile error)   :: {wireText txt}", false, st)
  let names := allNames src
  if names.isEmpty then
    if contains txt "sorry" then
      let gs ← extractGoals base src
      return (s!"OPEN  (unnamed, {gs.length} hole(s))\n" ++ "\n─── goal ───\n".intercalate gs, false, st)
    else return ("ACCEPT  (no named theorem — not axiom-gated)", true, st)
  let mut flagged : List String := []
  let mut sorried : List String := []
  for nm in names do
    let (_, axmsgs) ← runNew st s!"#print axioms {nm}"
    if hasError axmsgs then                      -- FIX 1: never swallow a probe error
      return (s!"REJECT  (axiom probe failed for '{nm}')   :: {wireText (← allText axmsgs)}", false, st)
    let av := axiomVerdict (← allText axmsgs)
    if av == "sorry" then sorried := sorried ++ [nm]
    else if contains av "nonstd:" then flagged := flagged ++ [s!"{nm} → {av}"]
  if ! flagged.isEmpty then                       -- FIX 2: any decl can trip the flag
    return (s!"⚠ FLAG   (non-standard axiom)   :: {"; ".intercalate flagged}", false, st)
  else if ! sorried.isEmpty then                  -- body-style: report goals, don't reject/accumulate
    let gs ← extractGoals base src
    return (s!"OPEN  (sorry in: {", ".intercalate sorried}) — {gs.length} goal(s):\n"
      ++ "\n─── next goal ───\n".intercalate gs, false, st)
  else
    return (s!"ACCEPT   :: {", ".intercalate names}", true, st)

unsafe def mainImpl (args : List String) : IO Unit := do
  Lean.initSearchPath (← Lean.findSysroot)
  enableInitializersExecution
  let lib := args.headD "Mathlib"
  let hb  := (args.drop 1).headD "200000"          -- FIX 3: maxHeartbeats
  let t0 ← IO.monoMsNow
  let mut base ← loadEnv s!"import {lib}\nset_option maxHeartbeats {hb}"
  let t1 ← IO.monoMsNow
  IO.println s!"[load] import {lib}: {t1 - t0} ms | modules={base.env.allImportedModuleNames.size} | maxHeartbeats={hb}"
  IO.println "[ready] paste a proof, then a line `%%` to submit (EOF also submits). env accumulates on ACCEPT."
  (← IO.getStdout).flush
  let stdin ← IO.getStdin
  let mut buf := ""
  let mut i := 0
  repeat
    let line ← stdin.getLine
    let eof := line.isEmpty
    if eof || line.trim == "%%" then
      let cmd := buf.trim
      if ! cmd.isEmpty then
        let a ← IO.monoMsNow
        let (v, acc, st) ← verify base cmd
        let z ← IO.monoMsNow
        let tag := if acc then "[+env] " else ""
        IO.println s!"[node {i}] {z - a} ms  {tag}{v}"
        if acc then base := { st with messages := {} }
        i := i + 1
        (← IO.getStdout).flush
      buf := ""
    else
      buf := buf ++ line
    if eof then break

unsafe def main (args : List String) : IO Unit := mainImpl args
