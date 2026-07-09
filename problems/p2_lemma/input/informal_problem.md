Two functions compute the sum 0 + 1 + ... + n in different ways:

  sumTo n      — direct recursion: sumTo 0 = 0, sumTo (n+1) = (n+1) + sumTo n
  sumAcc n a   — tail recursion with an accumulator: sumAcc 0 a = a,
                 sumAcc (n+1) a = sumAcc n (a + (n+1))

Prove that the accumulator version started at 0 agrees with the direct version:

  sumAcc n 0 = sumTo n   for all n.
