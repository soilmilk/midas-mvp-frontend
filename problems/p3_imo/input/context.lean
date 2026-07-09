-- recursive "double"
def dblA : Nat → Nat
  | 0     => 0
  | n + 1 => dblA n + 2

-- closed-form "double"
def dblB (n : Nat) : Nat := n + n

-- a function applied to both
def f (n : Nat) : Nat := n + 1
