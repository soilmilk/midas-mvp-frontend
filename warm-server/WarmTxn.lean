/- Transactional warm verifier — checkpoint / commit / rollback.
   A STEP = (lemma block, body block). The step COMMITS iff the lemma is
   ACCEPT-clean AND the body elaborates (OPEN or closed). On commit, ONLY the
   lemma folds into the running env; the body is verified then discarded. On any
   failure the tentative state is dropped and the env stays at the last checkpoint.

   Protocol (line-based, on stdin):
     %%LEMMA        begin lemma block   (lines until next marker)
     %%BODY         begin body block
     %%COMMIT       run the transaction; commit-or-rollback; print verdict
     %%PROBE <name> report whether <name> resolves in the CURRENT committed env
   `import …` lines are stripped (Mathlib + committed lemmas already resident). -/
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

def identOf (s : String) : String := (s.takeWhile fun c => c.isAlphanum || c == '_' || c == '.').toString

partial def collectNames : List String → List String → List String
  | "theorem" :: n :: rest, acc => collectNames rest (identOf n :: acc)
  | "lemma"   :: n :: rest, acc => collectNames rest (identOf n :: acc)
  | _ :: rest, acc => collectNames rest acc
  | [], acc => acc.reverse

def allNames (input : String) : List String :=
  let toks := ((input.splitOn " ").flatMap (·.splitOn "\n")).flatMap (·.splitOn "\t") |>.filter (· ≠ "")
  collectNames toks []

def leadWs (s : String) : String := (s.takeWhile fun c => c == ' ' || c == '\t').toString

def injectTraceState (src : String) : String :=
  let lines := src.splitOn "\n"
  "\n".intercalate <| lines.flatMap fun ln =>
    if ln.trim == "sorry" then [leadWs ln ++ "trace_state", ln] else [ln]

unsafe def extractGoals (base : Command.State) (src : String) : IO (List String) := do
  let (_, msgs) ← runNew base (injectTraceState src)
  let mut gs : List String := []
  for m in msgs do
    if m.severity != MessageSeverity.error then
      let t ← m.toString
      if contains t "⊢" then gs := gs ++ [t]
  return gs

def stripImports (src : String) : String :=
  "\n".intercalate <| (src.splitOn "\n").filter fun ln => ! ("import ".isPrefixOf ln.trim)

/-- String-returning trim (stable Substring API; avoids the String.trim→Slice deprecation). -/
def sTrim (s : String) : String := s.toSubstring.trim.toString

/-- Transaction: elaborate lemma, gate it, elaborate body against (base+lemma).
    Commit `stL` (lemma folded in) iff both pass; else return `base` unchanged.
    Returns (verdict text incl. full error, committed?, new base). -/
unsafe def runStep (base : Command.State) (idx : Nat) (lemmaSrc bodySrc : String)
    : IO (String × Bool × Command.State) := do
  let lsrc := stripImports lemmaSrc
  let bsrc := stripImports bodySrc
  let t0 ← IO.monoMsNow
  -- 1. lemma
  let (stL, msgsL) ← runNew base lsrc
  if hasError msgsL then
    return (s!"[step {idx}] ROLLBACK @lemma (compile error) ↓\n{← allText msgsL}", false, base)
  let names := allNames lsrc
  for nm in names do
    let (_, axm) ← runNew stL s!"#print axioms {nm}"
    if hasError axm then
      return (s!"[step {idx}] ROLLBACK @lemma-gate: axiom probe failed for '{nm}'", false, base)
    let av := axiomVerdict (← allText axm)
    if av == "sorry" then
      return (s!"[step {idx}] ROLLBACK @lemma: '{nm}' depends on sorryAx", false, base)
    else if contains av "nonstd:" then
      return (s!"[step {idx}] ROLLBACK @lemma: '{nm}' non-standard axiom ({av})", false, base)
  let t1 ← IO.monoMsNow
  -- 2. body against tentative (base + lemma)
  let (_, msgsB) ← runNew stL bsrc
  if hasError msgsB then
    return (s!"[step {idx}] ROLLBACK @body (compile error) ↓\n{← allText msgsB}", false, base)
  let t2 ← IO.monoMsNow
  let btxt ← allText msgsB
  let bodyState ← if contains btxt "sorry" then
      (do let gs ← extractGoals stL bsrc; pure s!"OPEN {gs.length} goal(s)")
    else pure "CLOSED"
  let kept := if names.isEmpty then "(none)" else ", ".intercalate names
  -- COMMIT: keep the lemma state stL, discard the body
  return (s!"[step {idx}] COMMIT   +env: {kept}   |   body {bodyState}   |   lemma {t1-t0}ms body {t2-t1}ms", true, stL)

unsafe def probeName (base : Command.State) (name : String) : IO String := do
  let (_, m) ← runNew base s!"#check @{name}"
  if hasError m then return s!"[probe] {name}  →  unknown (NOT in committed env)"
  else return s!"[probe] {name}  →  RESOLVES"

unsafe def mainImpl (args : List String) : IO Unit := do
  Lean.initSearchPath (← Lean.findSysroot)
  enableInitializersExecution
  let lib := args.headD "Mathlib"
  let hb  := (args.drop 1).headD "400000"
  let t0 ← IO.monoMsNow
  let mut base ← loadEnv s!"import {lib}\nset_option maxHeartbeats {hb}"
  let t1 ← IO.monoMsNow
  IO.println s!"[load] import {lib}: {t1 - t0} ms | modules={base.env.allImportedModuleNames.size}"
  IO.println "[ready] %%LEMMA / %%BODY / %%COMMIT / %%PROBE <name>"
  (← IO.getStdout).flush
  let stdin ← IO.getStdin
  let mut lemmaBuf := ""
  let mut bodyBuf := ""
  let mut mode := 0
  let mut idx := 0
  repeat
    let line ← stdin.getLine
    let eof := line.isEmpty
    let t := line.trim
    if t == "%%LEMMA" then
      mode := 1; lemmaBuf := ""
    else if t == "%%BODY" then
      mode := 2; bodyBuf := ""
    else if t == "%%COMMIT" then
      let (v, _committed, nb) ← runStep base idx lemmaBuf bodyBuf
      IO.println v
      base := nb
      idx := idx + 1
      lemmaBuf := ""; bodyBuf := ""; mode := 0
      (← IO.getStdout).flush
    else if "%%PROBE ".isPrefixOf t then
      IO.println (← probeName base (sTrim ((line.splitOn " ").getD 1 "")))
      (← IO.getStdout).flush
    else if ! eof then
      if mode == 1 then lemmaBuf := lemmaBuf ++ line
      else if mode == 2 then bodyBuf := bodyBuf ++ line
    if eof then break

unsafe def main (args : List String) : IO Unit := mainImpl args
