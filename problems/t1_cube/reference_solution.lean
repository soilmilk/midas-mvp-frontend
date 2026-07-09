-- reference (not read by the loop)
def cube (n : Nat) : Nat := n * n * n
theorem main : cube 3 = 27 := by decide
