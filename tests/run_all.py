#!/usr/bin/env python3
"""Run every deterministic Midas test gate sequentially.

No model API key is required. The backend-parity gate runs when the local warm
verifier and Mathlib LEAN_PATH are available and otherwise reports SKIP.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GATES = [
    ("Phase 1 verifier", "verifier/run_phase1.py"),
    ("Offline spine", "tests/test_offline.py"),
    ("Offline loop", "tests/test_loop_offline.py"),
    ("Automatic resume", "tests/test_resume_offline.py"),
    ("Hard Mode inputs", "tests/test_hardmode_inputs.py"),
    ("Reasoning protocol", "tests/test_reasoning_protocol.py"),
    ("Translation protocol", "tests/test_translation_protocol.py"),
    ("LLM usage accounting", "tests/test_usage_accounting.py"),
    ("Accepted dataset builder", "tests/test_accepted_dataset.py"),
    ("Hard Mode verifier", "tests/test_hardmode_verifier.py"),
    ("Hard Mode loop", "tests/test_hardmode_loop_offline.py"),
    ("Hard Mode CLI", "tests/test_hardmode_cli_offline.py"),
    ("Fresh/warm backend parity", "tests/test_hardmode_backend_parity.py"),
]


def main() -> int:
    results = []
    suite_started = time.perf_counter()

    for index, (label, relative_path) in enumerate(GATES, 1):
        print(
            f"\n{'=' * 88}\n"
            f"[{index}/{len(GATES)}] {label}: {relative_path}\n"
            f"{'=' * 88}",
            flush=True,
        )
        started = time.perf_counter()
        completed = subprocess.run(
            [sys.executable, str(ROOT / relative_path)],
            cwd=ROOT,
        )
        elapsed = time.perf_counter() - started
        results.append((label, relative_path, completed.returncode, elapsed))

    print(f"\n{'=' * 88}\nTEST SUITE SUMMARY\n{'=' * 88}")
    for label, relative_path, returncode, elapsed in results:
        status = "PASS" if returncode == 0 else "FAIL"
        print(
            f"{status:<5} {elapsed:>7.2f}s  {label:<28} {relative_path}"
        )

    failures = [result for result in results if result[2] != 0]
    total = time.perf_counter() - suite_started
    print("-" * 88)
    print(
        f"{len(results) - len(failures)}/{len(results)} gates passed "
        f"in {total:.2f}s"
    )
    if failures:
        print("MIDAS TEST SUITE: FAIL")
        return 1
    print("MIDAS TEST SUITE: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
