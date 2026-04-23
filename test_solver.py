"""Tests for the ILP solver framework."""

import os
import tempfile
from pathlib import Path

import yaml

from solver import ILPSolver, _parse_expression, load_config
from models import VariableSpec, ConstraintSpec


# ============================================================
# 1. 表达式解析测试
# ============================================================

class TestParseExpression:
    """Cover all expression syntax variants and edge cases."""

    def test_standard_lhs_rhs(self):
        # x + y >= 10 → x+y on left, 10 on right
        coeffs, sense, rhs = _parse_expression("2*x + y >= 10")
        assert coeffs == {"x": 2, "y": 1}
        assert sense == "ge"
        assert rhs == 10.0

    def test_le_constraint(self):
        coeffs, sense, rhs = _parse_expression("3*A + 4*B <= 100")
        assert coeffs == {"A": 3, "B": 4}
        assert sense == "le"
        assert rhs == 100.0

    def test_eq_constraint(self):
        coeffs, sense, rhs = _parse_expression("x + y == 10")
        assert coeffs == {"x": 1, "y": 1}
        assert sense == "eq"
        assert rhs == 10.0

    def test_bare_variables(self):
        # Variables without explicit coefficients
        coeffs, sense, rhs = _parse_expression("A + B >= 10")
        assert coeffs == {"A": 1, "B": 1}
        assert rhs == 10.0

    def test_negative_coefficient(self):
        coeffs, sense, rhs = _parse_expression("-x + 3*y <= 14")
        assert coeffs == {"x": -1, "y": 3}
        assert rhs == 14.0

    def test_decimal_coefficients(self):
        coeffs, sense, rhs = _parse_expression("1.5*x + 2.5*y >= 10")
        assert coeffs == {"x": 1.5, "y": 2.5}
        assert rhs == 10.0

    def test_variables_on_both_sides(self):
        # B >= 0.2*A + 0.2*C + 0.2*D → B - 0.2A - 0.2C - 0.2D >= 0
        coeffs, sense, rhs = _parse_expression("B >= 0.2*A + 0.2*C + 0.2*D")
        assert abs(coeffs["B"] - 1.0) < 1e-9
        assert abs(coeffs["A"] - (-0.2)) < 1e-9
        assert abs(coeffs["C"] - (-0.2)) < 1e-9
        assert abs(coeffs["D"] - (-0.2)) < 1e-9
        assert sense == "ge"
        assert rhs == 0.0, f"Expected rhs=0, got {rhs}"

    def test_large_coefficient_no_space(self):
        # The exact case that had a bug: 90*broccoli
        coeffs, sense, rhs = _parse_expression("90*broccoli + milk >= 60")
        assert coeffs == {"broccoli": 90, "milk": 1}
        assert rhs == 60.0, f"Expected rhs=60, got {rhs}"

    def test_coefficient_one_implicit(self):
        coeffs, sense, rhs = _parse_expression("x >= 5")
        assert coeffs == {"x": 1}
        assert rhs == 5.0

    def test_spaces_around_operator(self):
        coeffs, sense, rhs = _parse_expression("2*x+y>=10")
        assert coeffs == {"x": 2, "y": 1}
        assert rhs == 10.0

    def test_constant_on_left(self):
        # 10 - x <= 5 → -x <= -5 → x >= 5
        coeffs, sense, rhs = _parse_expression("10 - x <= 5")
        assert coeffs == {"x": -1}
        assert rhs == 5.0 - 10.0  # -5

    def test_single_variable_le(self):
        coeffs, sense, rhs = _parse_expression("x <= 10")
        assert coeffs == {"x": 1}
        assert sense == "le"
        assert rhs == 10.0

    def test_multiple_same_variable(self):
        # Same variable with coefficient syntax on both terms
        coeffs, sense, rhs = _parse_expression("1*x + 2*x >= 10")
        assert coeffs == {"x": 3}
        assert rhs == 10.0


# ============================================================
# 2. YAML 加载测试
# ============================================================

class TestLoadConfig:

    def _write_yaml(self, data: dict) -> str:
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(data, f, default_flow_style=False)
        f.close()
        return f.name

    def test_minimal_config(self):
        path = self._write_yaml({
            "name": "test",
            "sense": "minimize",
            "variables": {"x": {"lb": 0, "cat": "continuous"}},
            "objective": {"coefficients": {"x": 1}},
            "constraints": [{"name": "c1", "expression": "x >= 1"}],
        })
        cfg = load_config(path)
        os.unlink(path)
        assert cfg.name == "test"
        assert cfg.sense == "minimize"
        assert "x" in cfg.variables
        assert cfg.variables["x"].lb == 0
        assert cfg.constraints[0].sense == "ge"

    def test_binary_shorthand(self):
        path = self._write_yaml({
            "name": "test",
            "variables": {"z": {"cat": "binary"}},
            "objective": {"coefficients": {"z": 1}},
            "constraints": [],
        })
        cfg = load_config(path)
        os.unlink(path)
        assert cfg.variables["z"].lb == 0
        assert cfg.variables["z"].ub == 1
        assert cfg.variables["z"].cat == "binary"

    def test_structured_constraint(self):
        path = self._write_yaml({
            "name": "test",
            "variables": {"x": {}, "y": {}},
            "objective": {"coefficients": {"x": 1, "y": 1}},
            "constraints": [{"name": "c1", "coefficients": {"x": 2, "y": 1}, "sense": "le", "rhs": 10}],
        })
        cfg = load_config(path)
        os.unlink(path)
        c = cfg.constraints[0]
        assert c.coefficients == {"x": 2, "y": 1}
        assert c.sense == "le"
        assert c.rhs == 10.0

    def test_is_integer_detection(self):
        path = self._write_yaml({
            "name": "test",
            "variables": {"x": {"cat": "integer"}, "y": {"cat": "continuous"}},
            "objective": {"coefficients": {"x": 1, "y": 1}},
            "constraints": [],
        })
        cfg = load_config(path)
        os.unlink(path)
        assert cfg.is_integer is True


# ============================================================
# 3. 求解正确性测试
# ============================================================

class TestSolve:

    def _solve(self, data: dict):
        path = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(data, path, default_flow_style=False)
        path.close()
        solver = ILPSolver(path.name)
        result = solver.solve()
        os.unlink(path.name)
        return solver, result

    def test_simple_lp(self):
        solver, r = self._solve({
            "name": "simple_lp",
            "sense": "minimize",
            "variables": {"x": {"lb": 0, "cat": "continuous"}, "y": {"lb": 0, "cat": "continuous"}},
            "objective": {"coefficients": {"x": 2, "y": 3}},
            "constraints": [
                {"name": "c1", "expression": "x + y >= 10"},
                {"name": "c2", "expression": "2*x + y >= 14"},
            ],
        })
        assert r.status == "Optimal"
        assert abs(r.objective_value - 20.0) < 1e-4
        assert abs(r.variables["x"] - 10.0) < 1e-4
        assert abs(r.variables["y"] - 0.0) < 1e-4

    def test_maximization(self):
        solver, r = self._solve({
            "name": "max_test",
            "sense": "maximize",
            "variables": {"x": {"lb": 0, "cat": "continuous"}, "y": {"lb": 0, "cat": "continuous"}},
            "objective": {"coefficients": {"x": 3, "y": 2}},
            "constraints": [
                {"name": "c1", "expression": "x + y <= 10"},
                {"name": "c2", "expression": "x <= 8"},
            ],
        })
        assert r.status == "Optimal"
        # x=8 (capped), y=2 (remaining), obj=3*8+2*2=28
        assert abs(r.objective_value - 28.0) < 1e-4

    def test_infeasible(self):
        solver, r = self._solve({
            "name": "infeasible",
            "sense": "minimize",
            "variables": {"x": {"lb": 0, "cat": "continuous"}},
            "objective": {"coefficients": {"x": 1}},
            "constraints": [
                {"name": "c1", "expression": "x >= 10"},
                {"name": "c2", "expression": "x <= 5"},
            ],
        })
        assert r.status == "Infeasible"

    def test_binary_knapsack(self):
        solver, r = self._solve({
            "name": "knapsack",
            "sense": "maximize",
            "variables": {
                "x1": {"cat": "binary"}, "x2": {"cat": "binary"},
                "x3": {"cat": "binary"}, "x4": {"cat": "binary"},
            },
            "objective": {"coefficients": {"x1": 16, "x2": 22, "x3": 12, "x4": 8}},
            "constraints": [
                {"name": "weight", "coefficients": {"x1": 5, "x2": 7, "x3": 4, "x4": 3}, "sense": "le", "rhs": 14},
            ],
        })
        assert r.status == "Optimal"
        assert r.objective_value == 42.0

    def test_dual_values_in_lp(self):
        """Pure LP should produce non-trivial dual values."""
        solver, r = self._solve({
            "name": "dual_test",
            "sense": "minimize",
            "variables": {"x": {"lb": 0, "cat": "continuous"}, "y": {"lb": 0, "cat": "continuous"}},
            "objective": {"coefficients": {"x": 1, "y": 1}},
            "constraints": [
                {"name": "c1", "expression": "x + y >= 10"},
            ],
        })
        assert r.status == "Optimal"
        # The dual for a binding >= constraint should be non-zero
        dual = r.duals.get("c1", 0)
        assert abs(dual) > 1e-6, f"Expected non-zero dual, got {dual}"

    def test_reduced_costs_in_lp(self):
        """Variables at their bounds should have non-zero reduced cost."""
        solver, r = self._solve({
            "name": "rc_test",
            "sense": "minimize",
            "variables": {"x": {"lb": 0, "cat": "continuous"}, "y": {"lb": 0, "cat": "continuous"}},
            "objective": {"coefficients": {"x": 1, "y": 5}},
            "constraints": [
                {"name": "c1", "expression": "x + y >= 10"},
            ],
        })
        assert r.status == "Optimal"
        # y should be 0 (too expensive), so reduced cost should be non-zero
        rc_y = r.reduced_costs.get("y", 0)
        assert abs(rc_y) > 1e-6, f"Expected non-zero reduced cost for y, got {rc_y}"


# ============================================================
# 4. 报告输出测试
# ============================================================

class TestReports:

    def _make_solver(self):
        data = {
            "name": "report_test",
            "sense": "minimize",
            "variables": {"x": {"lb": 0, "cat": "continuous"}, "y": {"lb": 0, "cat": "continuous"}},
            "objective": {"coefficients": {"x": 2, "y": 3}},
            "constraints": [
                {"name": "demand", "expression": "x + y >= 10"},
                {"name": "capacity", "expression": "2*x + y >= 14"},
            ],
        }
        path = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(data, path, default_flow_style=False)
        path.close()
        solver = ILPSolver(path.name)
        solver.solve()
        os.unlink(path.name)
        return solver

    def test_report_solution_is_valid_yaml(self):
        solver = self._make_solver()
        content = solver.report_solution()
        data = yaml.safe_load(content)
        assert data["status"] == "Optimal"
        assert data["objective"] is not None
        assert "x" in data["variables"]

    def test_report_variable_is_valid_yaml(self):
        solver = self._make_solver()
        content = solver.report_variable()
        data = yaml.safe_load(content)
        assert len(data["variables"]) == 2
        v = data["variables"][0]
        assert "name" in v
        assert "value" in v
        assert "reduced_cost" in v
        assert "type" in v

    def test_report_constraint_is_valid_yaml(self):
        solver = self._make_solver()
        content = solver.report_constraint()
        data = yaml.safe_load(content)
        assert len(data["constraints"]) == 2
        c = data["constraints"][0]
        assert "name" in c
        assert "lhs" in c
        assert "rhs" in c
        assert "slack" in c
        assert "dual" in c
        assert "binding" in c

    def test_report_objective_is_valid_yaml(self):
        solver = self._make_solver()
        content = solver.report_objective()
        data = yaml.safe_load(content)
        assert data["total"] == 20.0
        assert len(data["contributions"]) == 2
        # Check percentage sums to ~100
        total_pct = sum(c["percentage"] for c in data["contributions"])
        assert abs(total_pct - 100.0) < 1.0


# ============================================================
# 5. 示例文件集成测试
# ============================================================

class TestExamples:
    """Verify all example YAML files solve correctly."""

    def test_knapsack(self):
        solver = ILPSolver("examples/knapsack.yaml")
        r = solver.solve()
        assert r.status == "Optimal"
        assert r.objective_value == 42.0
        assert r.variables["x2"] == 1.0
        assert r.variables["x3"] == 1.0
        assert r.variables["x4"] == 1.0

    def test_production(self):
        solver = ILPSolver("examples/production.yaml")
        r = solver.solve()
        assert r.status == "Optimal"
        assert abs(r.objective_value - 20.0) < 1e-4

    def test_factory(self):
        solver = ILPSolver("examples/factory.yaml")
        r = solver.solve()
        assert r.status == "Optimal"
        assert r.objective_value > 31000
        assert r.variables["D"] == 80.0  # D always at max

    def test_diet(self):
        solver = ILPSolver("examples/diet.yaml")
        r = solver.solve()
        assert r.status == "Optimal"
        assert r.objective_value > 0
        # vitamin_c constraint should be binding (broccoli > 0)
        assert r.variables["broccoli"] > 0

    def test_factory_rhs_correct(self):
        """Verify no parsing bug: min_B_share rhs should be 0."""
        solver = ILPSolver("examples/factory.yaml")
        solver.solve()
        # The min_B_share constraint: B >= 0.2*A + 0.2*C + 0.2*D → rhs = 0
        for c in solver.config.constraints:
            if c.name == "min_B_share":
                assert c.rhs == 0.0, f"min_B_share rhs should be 0, got {c.rhs}"

    def test_diet_vitamin_c_rhs_correct(self):
        """Verify 90*broccoli + milk >= 60 parses with rhs=60."""
        solver = ILPSolver("examples/diet.yaml")
        solver.solve()
        for c in solver.config.constraints:
            if c.name == "vitamin_c":
                assert c.rhs == 60.0, f"vitamin_c rhs should be 60, got {c.rhs}"


# ============================================================
# 运行
# ============================================================

if __name__ == "__main__":
    import traceback

    all_classes = [TestParseExpression, TestLoadConfig, TestSolve, TestReports, TestExamples]
    passed = 0
    failed = 0

    for cls in all_classes:
        instance = cls()
        methods = [m for m in dir(instance) if m.startswith("test_")]
        for method in methods:
            try:
                getattr(instance, method)()
                print(f"  PASS  {cls.__name__}.{method}")
                passed += 1
            except Exception as e:
                print(f"  FAIL  {cls.__name__}.{method}: {e}")
                traceback.print_exc()
                failed += 1

    print(f"\n{'='*50}")
    print(f"Results: {passed} passed, {failed} failed, {passed + failed} total")
    if failed == 0:
        print("All tests passed.")
