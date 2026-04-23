#!/usr/bin/env python3
"""Validate all example YAML problems against known optimal solutions."""

import sys
import os
from solver import ILPSolver

# (filename, expected_status, expected_objective)
CASES = [
    ("classic_production.yaml", "Optimal", 180),
    ("classic_diet.yaml", "Optimal", 7),
    ("binary_knapsack_3item.yaml", "Optimal", 7),
    ("set_cover.yaml", "Optimal", 5),
    ("facility_location.yaml", "Optimal", 530),
    ("assignment.yaml", "Optimal", 9),
    ("knapsack.yaml", "Optimal", 42),
    ("production.yaml", "Optimal", 20),
    ("diet.yaml", "Optimal", 9.5963091),
    ("factory.yaml", "Optimal", 31660),
    ("unbounded.yaml", "Unbounded", None),
    ("infeasible.yaml", "Infeasible", None),
]


def main():
    examples_dir = os.path.join(os.path.dirname(__file__), "examples")
    passed = 0
    failed = 0

    for fname, exp_status, exp_obj in CASES:
        path = os.path.join(examples_dir, fname)
        if not os.path.exists(path):
            print(f"[SKIP] {fname} — file not found")
            failed += 1
            continue

        solver = ILPSolver(path)
        result = solver.solve()
        obj_str = f"{result.objective_value:.4f}" if result.objective_value is not None else "None"

        status_ok = result.status == exp_status
        obj_ok = True
        if exp_obj is not None:
            obj_ok = abs(result.objective_value - exp_obj) < 1e-4

        if status_ok and obj_ok:
            print(f"[PASS] {fname:35s} status={result.status:10s} obj={obj_str}")
            passed += 1
        else:
            print(f"[FAIL] {fname:35s} status={result.status:10s} obj={obj_str}")
            if not status_ok:
                print(f"       status: expected {exp_status}")
            if not obj_ok and exp_obj is not None:
                print(f"       objective: expected {exp_obj}")
            failed += 1

    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
