import re
import argparse
from pathlib import Path

import yaml
import pulp

from models import VariableSpec, ConstraintSpec, ObjectiveSpec, ProblemConfig, SolutionResult
from visualizer import (
    visualize,
    plot_2d_region,
    plot_variable_values,
    plot_resource_utilization,
    plot_objective_breakdown,
    plot_constraint_heatmap,
    plot_constraint_gap,
)


_OP_MAP = {">=": "ge", "<=": "le", "==": "eq"}
_CAT_MAP = {"continuous": pulp.LpContinuous, "integer": pulp.LpInteger, "binary": pulp.LpBinary}

_VISUAL_FLAGS = [
    "visual_region",
    "visual_value",
    "visual_resource",
    "visual_objective",
    "visual_heatmap",
    "visual_gap",
]
_REPORT_FLAGS = [
    "report_solution",
    "report_variable",
    "report_constraint",
    "report_objective",
]


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
        consumed = s
        # Match terms like "2*x", "-x", "+3.5*x", "x", but not bare numbers like "10"
        for match in re.finditer(r"([+-]?\s*\d*\.?\d*)\s*\*\s*([A-Za-z_]\w*)", s):
            coef_str, var = match.group(1).replace(" ", ""), match.group(2)
            coef = float(coef_str) if coef_str and coef_str not in ("+", "-") else (1.0 if coef_str != "-" else -1.0)
            coeffs[var] = coeffs.get(var, 0) + coef
            consumed = consumed.replace(match.group(0), "", 1)
        # Match bare variable names (no * and no coefficient), e.g. "A", "+ B", "- C"
        for match in re.finditer(r"(?:^|(?<=[-+\s]))([+-]?)\s*([A-Za-z_]\w*)(?!\s*\*|\d)", s):
            var = match.group(2)
            if var not in coeffs:
                sign = match.group(1)
                coeffs[var] = -1.0 if sign == "-" else 1.0
                consumed = consumed.replace(match.group(0), "", 1)
        # Extract standalone constant (number not adjacent to a variable)
        remaining = re.sub(r"[A-Za-z_]\w*", "", consumed)
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

        reduced_costs = {name: v.dj for name, v in self._vars.items()}

        self.result = SolutionResult(
            status=pulp.LpStatus[status],
            objective_value=pulp.value(prob.objective),
            variables={name: v.varValue for name, v in self._vars.items()},
            duals=duals,
            slacks=slacks,
            reduced_costs=reduced_costs,
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

    # ============================================================
    # YAML 报告方法
    # ============================================================

    def report_solution(self) -> str:
        r = self.result
        lines = [
            f"# Solution Report — {self.config.name}",
            f"# status: solver termination status (Optimal / Infeasible / Unbounded)",
            f"# objective: optimal objective value (null if no feasible solution)",
            f"# variables: optimal value of each decision variable",
            f"status: {r.status}",
            f"objective: {r.objective_value}",
            f"variables:",
        ]
        for k, v in r.variables.items():
            v_str = "null" if v is None else round(v, 6)
            lines.append(f"  {k}: {v_str}")
        return "\n".join(lines) + "\n"

    def report_variable(self) -> str:
        r = self.result
        cfg = self.config
        lines = [
            f"# Variable Detail Report — {self.config.name}",
            f"# name: variable name",
            f"# value: optimal value of this variable",
            f"# reduced_cost: amount by which objective coefficient must improve before this variable enters the basis",
            f"#   (0 for basic variables; non-zero for variables at their bounds)",
            f"# lower_bound / upper_bound: variable bounds from the problem definition",
            f"# type: variable category (continuous / integer / binary)",
            f"variables:",
        ]
        for name, spec in cfg.variables.items():
            val = r.variables.get(name)
            rc = r.reduced_costs.get(name, 0)
            v_str = "null" if val is None else round(val, 6)
            ub_str = "null" if spec.ub is None else spec.ub
            lines.append(f"  - name: {name}")
            lines.append(f"    value: {v_str}")
            lines.append(f"    reduced_cost: {round(rc, 6)}")
            lines.append(f"    lower_bound: {spec.lb}")
            lines.append(f"    upper_bound: {ub_str}")
            lines.append(f"    type: {spec.cat}")
        return "\n".join(lines) + "\n"

    def report_constraint(self) -> str:
        r = self.result
        cfg = self.config
        lines = [
            f"# Constraint Detail Report — {self.config.name}",
            f"# lhs: left-hand side value (sum of coefficient * variable_value at optimal)",
            f"# rhs: right-hand side constant from the problem definition",
            f"# slack: rhs - lhs for <= constraints; lhs - rhs for >= constraints",
            f"#   slack=0 means the constraint is binding (active at equality)",
            f"# dual: shadow price — change in objective per unit increase in rhs",
            f"#   (from LP relaxation for ILP problems, not exact)",
            f"# binding: true if constraint is tight (|slack| ≈ 0)",
            f"constraints:",
        ]
        for c in cfg.constraints:
            lhs = sum(c.coefficients.get(v, 0) * (r.variables.get(v) or 0) for v in r.variables)
            slack = r.slacks.get(c.name, 0)
            dual = r.duals.get(c.name)
            d_str = "null" if dual is None else round(dual, 6)
            lines.append(f"  - name: {c.name}")
            lines.append(f"    lhs: {round(lhs, 6)}")
            lines.append(f"    rhs: {c.rhs}")
            lines.append(f"    slack: {round(slack, 6)}")
            lines.append(f"    dual: {d_str}")
            lines.append(f"    binding: {str(abs(slack) < 1e-9).lower()}")
        return "\n".join(lines) + "\n"

    def report_objective(self) -> str:
        r = self.result
        cfg = self.config
        total = r.objective_value or 0
        lines = [
            f"# Objective Decomposition Report — {self.config.name}",
            f"# total: overall optimal objective value",
            f"# contributions: breakdown by variable",
            f"#   variable: variable name",
            f"#   coefficient: objective coefficient for this variable",
            f"#   value: optimal value of this variable",
            f"#   contribution: coefficient × value = this variable's share of the objective",
            f"#   percentage: |contribution| / |total| × 100",
            f"total: {round(total, 6)}",
            f"contributions:",
        ]
        for var, coef in cfg.objective.coefficients.items():
            val = r.variables.get(var) or 0
            contrib = coef * val
            pct = round(abs(contrib) / abs(total) * 100, 1) if abs(total) > 1e-9 else 0
            lines.append(f"  - variable: {var}")
            lines.append(f"    coefficient: {coef}")
            lines.append(f"    value: {round(val, 6)}")
            lines.append(f"    contribution: {round(contrib, 6)}")
            lines.append(f"    percentage: {pct}")
        return "\n".join(lines) + "\n"


def _save_yaml(content: str, path: Path) -> None:
    with open(path, "w") as f:
        f.write(content)


def main():
    parser = argparse.ArgumentParser(description="ILP Solver (PuLP + CBC)")
    parser.add_argument("config", help="Path to YAML problem definition")

    # Visual flags
    parser.add_argument("--visualize", "-v", action="store_true",
                        help="Generate all visualizations")
    for flag in _VISUAL_FLAGS:
        name = flag.replace("_", "-")
        parser.add_argument(f"--{name}", action="store_true", help=f"Generate {flag} visualization")

    # Report flags
    for flag in _REPORT_FLAGS:
        name = flag.replace("_", "-")
        parser.add_argument(f"--{name}", action="store_true", help=f"Generate {flag} report (YAML)")

    args = parser.parse_args()

    solver = ILPSolver(args.config)
    solver.solve()
    solver.print_result()
    print(solver.sensitivity_report())

    tmp_dir = Path("tmp")
    stem = Path(args.config).stem

    # -v implies all visual flags
    any_visual = args.visualize or any(getattr(args, f) for f in _VISUAL_FLAGS)

    if any_visual:
        tmp_dir.mkdir(exist_ok=True)

        # Default -v behavior: region (2-var) or summary (multi-var)
        if args.visualize and not any(getattr(args, f) for f in _VISUAL_FLAGS):
            out = tmp_dir / f"{stem}_result.png"
            visualize(solver.config, solver.result, str(out))
            print(f"\nVisualization saved to {out}")
        else:
            if args.visual_region:
                out = tmp_dir / f"{stem}_region.png"
                plot_2d_region(solver.config, solver.result, str(out))
                print(f"Region plot saved to {out}")
            if args.visual_value:
                out = tmp_dir / f"{stem}_value.png"
                plot_variable_values(solver.config, solver.result, str(out))
                print(f"Variable values plot saved to {out}")
            if args.visual_resource:
                out = tmp_dir / f"{stem}_resource.png"
                plot_resource_utilization(solver.config, solver.result, str(out))
                print(f"Resource utilization plot saved to {out}")
            if args.visual_objective:
                out = tmp_dir / f"{stem}_objective.png"
                plot_objective_breakdown(solver.config, solver.result, str(out))
                print(f"Objective breakdown plot saved to {out}")
            if args.visual_heatmap:
                out = tmp_dir / f"{stem}_heatmap.png"
                plot_constraint_heatmap(solver.config, solver.result, str(out))
                print(f"Constraint heatmap saved to {out}")
            if args.visual_gap:
                out = tmp_dir / f"{stem}_gap.png"
                plot_constraint_gap(solver.config, solver.result, str(out))
                print(f"Constraint gap plot saved to {out}")

    # Reports
    any_report = any(getattr(args, f) for f in _REPORT_FLAGS)
    if any_report:
        tmp_dir.mkdir(exist_ok=True)

        if args.report_solution:
            out = tmp_dir / f"{stem}_solution.yaml"
            _save_yaml(solver.report_solution(), out)
            print(f"Solution report saved to {out}")
        if args.report_variable:
            out = tmp_dir / f"{stem}_variable.yaml"
            _save_yaml(solver.report_variable(), out)
            print(f"Variable report saved to {out}")
        if args.report_constraint:
            out = tmp_dir / f"{stem}_constraint.yaml"
            _save_yaml(solver.report_constraint(), out)
            print(f"Constraint report saved to {out}")
        if args.report_objective:
            out = tmp_dir / f"{stem}_objective.yaml"
            _save_yaml(solver.report_objective(), out)
            print(f"Objective report saved to {out}")


if __name__ == "__main__":
    main()
