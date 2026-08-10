The following problem statement is written in tex:

Let
\[
G=\{0,1,\ldots,44\}\times\{0,1,\ldots,44\}
\]
be the set of cells of a \(45\times45\) grid. The first coordinate
denotes the row of a cell, and the second coordinate denotes its
column.

Two cells are called \emph{horizontally adjacent} if they are in the
same row and their column coordinates differ by \(1\). They are called
\emph{vertically adjacent} if they are in the same column and their
row coordinates differ by \(1\). Two cells share a side if they are
either horizontally or vertically adjacent.

An \emph{echidna tour} is a bijection
\[
E:\{0,1,\ldots,2024\}\longrightarrow G
\]
such that \(E(k)\) and \(E(k+1)\) share a side for every
\[
k\in\{0,1,\ldots,2023\}.
\]
Thus, \(E(k)\) is the cell visited at time \(k\), and every cell is
visited exactly once.

Prove that for every echidna tour \(E\), there exists a bijection
\[
N:G\longrightarrow\{1,2,\ldots,2025\}
\]
such that the following two properties hold.

\begin{enumerate}
    \item If \(C,D\in G\) are horizontally adjacent and
    \[
    N(C)<N(D),
    \]
    then \(D\) was visited before \(C\). Equivalently,
    \[
    E^{-1}(D)<E^{-1}(C).
    \]

    \item If \(C,D\in G\) are vertically adjacent and
    \[
    N(C)<N(D),
    \]
    then \(D\) was visited after \(C\). Equivalently,
    \[
    E^{-1}(C)<E^{-1}(D).
    \]
\end{enumerate}

In other words, among any two horizontally adjacent cells, the cell
containing the larger number was visited earlier, while among any two
vertically adjacent cells, the cell containing the larger number was
visited later.