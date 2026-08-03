-- One legal blackboard move.
def BlackboardMove
    (s t : Multiset ℕ) : Prop :=
  ∃ (rest : Multiset ℕ) (m n : ℕ),
    1 < m ∧
    1 < n ∧
    s = m ::ₘ n ::ₘ rest ∧
    t =
      Nat.gcd m n ::ₘ
      (Nat.lcm m n / Nat.gcd m n) ::ₘ
      rest

-- The board t can be obtained from s after zero or more moves.
def Reachable
    (s t : Multiset ℕ) : Prop :=
  Relation.ReflTransGen BlackboardMove s t

-- No further legal move can be made from s.
def Terminal
    (s : Multiset ℕ) : Prop :=
  ¬ ∃ t, BlackboardMove s t

-- There is no infinite sequence of legal moves starting from s.
def TerminatesFrom
    (s : Multiset ℕ) : Prop :=
  Acc (fun t s => BlackboardMove s t) s
