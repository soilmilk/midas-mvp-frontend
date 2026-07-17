Let s be a blackboard containing exactly 2026 natural numbers, each greater than 1.

A legal move consists of selecting two entries m,n > 1 from two different positions and replacing them with

gcd(m,n) and lcm(m,n)/gcd(m,n).

A board t is called reachable from s if it can be obtained from s after finitely many legal moves, including zero moves. A board is called terminal if no legal move is possible.

Prove that there exists a natural number M > 1 such that:

Every sequence of legal moves starting from s terminates after finitely many moves.
At least one terminal board is reachable from s.
Every terminal board reachable from s contains exactly one entry greater than 1, and that entry is M.

Therefore, the final value M does not depend on which legal moves are chosen.