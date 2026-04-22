import re
import argparse
from pathlib import Path

import yaml
import pulp

from models import VariableSpec, ConstraintSpec, ObjectiveSpec, ProblemConfig, SolutionResult
from visualizer import visualize


_OP_MAP = {">=": "ge", "<=": "le", "==": "eq"}
_CAT_MAP = {"continuous": pulp.LpContinuous, "integer": pulp.LpInteger, "binary": pulp.LpBinary}


def _parse_expression(expr_str: str) -> tuple[dict[str, float], str, float]:
    """Parse a constraint expression like '2*x + y >= 10' or 'B >= 0.2*A + 0.3*C'.

    Both sides may contain variable terms. Variables on the RHS are moved to LHS
    (with negated coefficients), so the result is always (coefficients, sense, rhs)
    where rhs is a constant.
    """
    expr_str = expr_str.strip()

    # Find comparison operator
    cmp_match = re.search(r"(>=|<=|==)", expr_str)
    if not cmp_match:
        raise ValueError(f"Cannot parse comparison in: '{expr_str}'")
    op = cmp_match.group(1)
    left_str = expr_str[: cmp_match.start()].strip()
    right_str = expr_str[cmp_match.end() :].strip()

    # Parse variable terms from a string into {var: coefficient}
    def _extract_coeffs(s: str) -> tuple[dict[str, float], float]:
        coeffs: dict[str, float] = {}
        # Match terms like "2*x", "-x", "+3.5*x", "x", but not bare numbers like "10"
        for match in re.finditer(r"([+-]?\s*\d*\.?\d*)\s*\*\s*([A-Za-z_]\w*)", s):
            coef_str, var = match.group(1).replace(" ", ""), match.group(2)
            coef = float(coef_str) if coef_str and coef_str not in ("+", "-") else (1.0 if coef_str != "-" else -1.0)
            coeffs[var] = coeffs.get(var, 0) + coef
        # Match bare variable names (no * and no coefficient), e.g. "A", "+ B", "- C"
        for match in re.finditer(r"(?:^|(?<=[-+\s]))([+-]?)\s*([A-Za-z_]\w*)(?!\s*\*|\d)", s):
            var = match.group(2)
            if var not in coeffs:
                sign = match.group(1)
                coeffs[var] = -1.0 if sign == "-" else 1.0
        # Extract standalone constant (number not adjacent to a variable)
        remaining = re.sub(r"[A-Za-z_]\w*", "", s)
        const_match = re.search(r"[-+]?\s*\d+\.?\d*", remaining)
        const = float(const_match.group().replace(" ", "")) if const_match else 0.0
        return coeffs, const

    left_coeffs, left_const = _extract_coeffs(left_str)
    right_coeffs, right_const = _extract_coeffs(right_str)

    # Move everything to LHS: left - right ~ 0 => left ~ right
    coefficients: dict[str, float] = {}
    for var, coef in left_coeffs.items():
        coefficients[var] = coefficients.get(var, 0) + coef
    for var, coef in right_coeffs.items():
        coefficients[var] = coefficients.get(var, 0) - coef
    rhs = right_const - left_const

    return coefficients, _OP_MAP[op], rhs


def _parse_variable(name: str, spec: dict) -> VariableSpec:
    if spec.get("cat") == "binary":
        return VariableSpec(name=name, lb=0, ub=1, cat="binary")
    return VariableSpec(
        name=name,
        lb=spec.get("lb", 0),
        ub=spec.get("ub"),
        cat=spec.get("cat", "continuous"),
    )


def _parse_constraint(raw: dict, index: int) -> ConstraintSpec:
    name = raw.get("name", f"c{index}")
    if "expression" in raw:
        coefficients, sense, rhs = _parse_expression(raw["expression"])
    else:
        coefficients = dict(raw["coefficients"])
        sense = raw["sense"]
        rhs = float(raw["rhs"])
    return ConstraintSpec(name=name, coefficients=coefficients, sense=sense, rhs=rhs)


def load_config(path: str) -> ProblemConfig:
    with open(path) as f:
        data = yaml.safe_load(f)

    variables = {name: _parse_variable(name, spec) for name, spec in data["variables"].items()}
    objective = ObjectiveSpec(coefficients=data["objective"]["coefficients"])
    constraints = [_parse_constraint(c, i) for i, c in enumerate(data.get("constraints", []))]

    return ProblemConfig(
        name=data["name"],
        sense=data.get("sense", "minimize"),
        variables=variables,
        objective=objective,
        constraints=constraints,
    )


class ILPSolver:
    def __init__(self, config_path: str):
        self.config = load_config(config_path)
        self._prob: pulp.LpProblem | None = None
        self._vars: dict[str, pulp.LpVariable] = {}
        self.result: SolutionResult | None = None

    def solve(self) -> SolutionResult:
        cfg = self.config
        sense = pulp.LpMinimize if cfg.sense == "minimize" else pulp.LpMaximize
        prob = pulp.LpProblem(cfg.name, sense)

        # Variables
        for name, spec in cfg.variables.items():
            self._vars[name] = pulp.LpVariable(
                name, lowBound=spec.lb, upBound=spec.ub, cat=_CAT_MAP[spec.cat]
            )

        # Objective
        obj_expr = pulp.lpSum(
            coef * self._vars[var] for var, coef in cfg.objective.coefficients.items()
        )
        prob += obj_expr, "objective"

        # Constraints
        for c in cfg.constraints:
            lhs = pulp.lpSum(coef * self._vars[var] for var, coef in c.coefficients.items())
            prob += (lhs <= c.rhs if c.sense == "le"
                     else lhs >= c.rhs if c.sense == "ge"
                     else lhs == c.rhs), c.name

        self._prob = prob
        status = prob.solve()

        duals, slacks = {}, {}
        if prob.constraints:
            for name, constraint in prob.constraints.items():
                duals[name] = constraint.pi
                slacks[name] = constraint.slack

        self.result = SolutionResult(
            status=pulp.LpStatus[status],
            objective_value=pulp.value(prob.objective),
            variables={name: v.varValue for name, v in self._vars.items()},
            duals=duals,
            slacks=slacks,
        )
        return self.result

    def print_result(self) -> None:
        r = self.result
        if r is None:
            print("Problem not solved yet.")
            return
        print(f"Problem: {self.config.name}")
        print(f"Status : {r.status}")
        if r.objective_value is not None:
            print(f"Objective = {r.objective_value}")
        for name, val in r.variables.items():
            print(f"  {name} = {val}")

    def sensitivity_report(self) -> str:
        r = self.result
        if r is None:
            return "Problem not solved yet."

        lines = ["\nSensitivity Report", "-" * 40]
        if self.config.is_integer:
            lines.append("Note: dual values are from LP relaxation (not exact for ILP).")
        lines.append(f"{'Constraint':<20} {'Slack':>10} {'Dual':>10}")
        for name in r.slacks:
            slack = r.slacks[name]
            dual = r.duals.get(name)
            dual_str = f"{dual:.4f}" if dual is not None else "N/A"
            lines.append(f"{name:<20} {slack:>10.4f} {dual_str:>10}")
        return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="ILP Solver (PuLP + CBC)")
    parser.add_argument("config", help="Path to YAML problem definition")
    parser.add_argument("--visualize", "-v", action="store_true", help="Generate visualization")
    args = parser.parse_args()

    solver = ILPSolver(args.config)
    solver.solve()
    solver.print_result()
    print(solver.sensitivity_report())

    if args.visualize:
        tmp_dir = Path("tmp")
        tmp_dir.mkdir(exist_ok=True)
        out = tmp_dir / (Path(args.config).stem + "_result.png")
        visualize(solver.config, solver.result, str(out))
        print(f"\nVisualization saved to {out}")


if __name__ == "__main__":
    main()
