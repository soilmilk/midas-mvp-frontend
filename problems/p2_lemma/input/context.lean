-- direct recursive sum 0 + 1 + ... + n
def sumTo : Nat → Nat
  | 0     => 0
  | n + 1 => (n + 1) + sumTo n

-- tail-recursive sum with an accumulator
def sumAcc : Nat → Nat → Nat
  | 0,     a => a
  | n + 1, a => sumAcc n (a + (n + 1))
