# LP Explorer

A unified ILP (Integer Linear Programming) solver framework based on PuLP + CBC. Define your optimization problem in a YAML file, then solve, analyze, and visualize with one command.

## Features

- **YAML-based problem definition** — describe variables, objective, and constraints in a simple config file
- **Flexible constraint syntax** — use either human-readable expressions (`x + y >= 10`) or structured dicts
- **Variable types** — continuous, integer, and binary
- **Sensitivity analysis** — constraint slack and dual values (shadow prices)
- **Visualization** — 2D feasible region plot for 2-variable problems; variable value + slack bar charts for general problems

## Quick Start

### Install dependencies

```bash
pip install -r requirements.txt
```

### Run an example

```bash
# Basic solve
python solver.py examples/knapsack.yaml

# Solve with visualization
python solver.py examples/production.yaml -v
```

### Python API

```python
from solver import ILPSolver

solver = ILPSolver("examples/knapsack.yaml")
result = solver.solve()
solver.print_result()
print(solver.sensitivity_report())
```

## YAML Format

```yaml
name: my_problem
sense: minimize            # minimize or maximize

variables:
  x: { lb: 0, ub: 10, cat: integer }    # cat: continuous / integer / binary
  y: { lb: 0, cat: continuous }
  z: { cat: binary }                     # binary shorthand

objective:
  coefficients: { x: 2, y: 3, z: 1 }

constraints:
  # Expression syntax
  - name: demand
    expression: x + y >= 10

  # Structured syntax
  - name: capacity
    coefficients: { x: 2, y: 1 }
    sense: le               # le (<=), ge (>=), eq (==)
    rhs: 14
```

### Variable shorthand

| Field    | Default       | Description                  |
|----------|---------------|------------------------------|
| `lb`     | `0`           | Lower bound                  |
| `ub`     | `None`        | Upper bound (unbounded if omitted) |
| `cat`    | `continuous`  | `continuous`, `integer`, or `binary` |

When `cat: binary`, `lb=0` and `ub=1` are set automatically.

---

## Code Walkthrough

### Architecture Overview

```
YAML file
   │
   ▼
load_config() ── parses YAML → ProblemConfig data object
   │
   ▼
ILPSolver.solve() ── translates ProblemConfig into PuLP model → calls CBC solver
   │
   ▼
SolutionResult ── solver output (variable values, objective, slack, duals)
   │
   ▼
visualize() ── generates 2D feasible region or summary bar charts
```

Three modules, each with a single responsibility:
- **models.py** — pure data structures, no solver logic
- **solver.py** — core logic: parse input → build model → solve → output
- **visualizer.py** — generate charts from solver results

---

### models.py — Data Models

> Pure data structures for problem configuration and solver results. Completely decoupled from the solver.

#### VariableSpec — Variable specification

```python
@dataclass
class VariableSpec:
    name: str                              # Variable name, e.g. "x", "y1"
    lb: float = 0                          # Lower bound, default 0 (i.e. x >= 0)
    ub: Optional[float] = None             # Upper bound, None means unbounded
    cat: str = "continuous"                # Type: continuous / integer / binary
```

Maps to the YAML:

```yaml
variables:
  x: { lb: 0, ub: 10, cat: integer }
```

`lb=0` is the most common default in operations research (quantities, production amounts can't be negative).

#### ConstraintSpec — Constraint specification

```python
@dataclass
class ConstraintSpec:
    name: str                              # Constraint name, used in output and debugging
    coefficients: dict[str, float]         # Variable coefficients, e.g. {"x": 2, "y": 1}
    sense: str                             # "le"(<=) / "ge"(>=) / "eq"(==)
    rhs: float                             # Right-hand side, e.g. 14
```

Represents a mathematical constraint like `2x + 1y <= 14`. Both YAML syntaxes (expression and structured) are normalized to this format.

#### ObjectiveSpec — Objective function

```python
@dataclass
class ObjectiveSpec:
    coefficients: dict[str, float]         # Objective coefficients, e.g. {"x": 2, "y": 3}
```

Represents `min 2x + 3y`. Min/max direction is controlled by `ProblemConfig.sense`.

#### ProblemConfig — Complete problem configuration

```python
@dataclass
class ProblemConfig:
    name: str                              # Problem name
    sense: str                             # "minimize" / "maximize"
    variables: dict[str, VariableSpec]     # All variables
    objective: ObjectiveSpec               # Objective function
    constraints: list[ConstraintSpec]      # All constraints

    @property
    def is_integer(self) -> bool:
        return any(v.cat in ("integer", "binary") for v in self.variables.values())
```

The `is_integer` property is used in the sensitivity report to warn that dual values come from LP relaxation.

#### SolutionResult — Solver output

```python
@dataclass
class SolutionResult:
    status: str                            # "Optimal" / "Infeasible" / "Unbounded", etc.
    objective_value: Optional[float]       # Optimal objective value, None if infeasible
    variables: dict[str, Optional[float]]  # Variable values, e.g. {"x": 10.0, "y": 0.0}
    duals: dict[str, Optional[float]]      # Shadow prices (dual values) per constraint
    slacks: dict[str, float]               # Slack per constraint
```

- `slacks["demand"] = -0.0` means the constraint is binding (active at equality)
- `duals["demand"] = 2.0` means relaxing the demand constraint by 1 unit changes the objective by 2
- For ILP problems, `duals` come from the LP relaxation and are not exact

---

### solver.py — Core Solver

> Translates YAML config into a PuLP model and calls CBC to solve.

#### Global lookup tables

```python
_OP_MAP = {">=": "ge", "<=": "le", "==": "eq"}          # Comparison operator → internal sense
_CAT_MAP = {"continuous": pulp.LpContinuous,             # YAML cat string → PuLP constant
            "integer": pulp.LpInteger,
            "binary": pulp.LpBinary}
```

#### _parse_expression() — Expression parser

```python
def _parse_expression(expr_str: str) -> tuple[dict[str, float], str, float]:
```

Converts `"2*x + y >= 10"` into `({"x": 2, "y": 1}, "ge", 10)`.

**Step 1: Extract comparison operator and RHS**

```python
cmp_match = re.search(r"(>=|<=|==)\s*([-+]?\d*\.?\d+)\s*$", expr_str)
op = cmp_match.group(1)         # ">="
rhs = float(cmp_match.group(2)) # 10.0
lhs = expr_str[:cmp_match.start()].strip()  # "2*x + y"
```

The regex anchors at end of string (`$`) to capture the final comparison.

**Step 2: Extract coefficients and variables from LHS**

```python
for match in re.finditer(r"([+-]?\s*\d*\.?\d*)\*?\s*(\w+)", lhs):
    coef_str, var = match.group(1).replace(" ", ""), match.group(2)
```

Matches each term, handling:
- `"2*x"` → coef=2, var=x
- `"-x"` → coef=-1, var=x
- `"+3.5*y"` → coef=3.5, var=y
- `"x"` → coef=1, var=x

```python
    if re.match(r"^[+-]?$", coef_str):
        coef = 1.0 if coef_str in ("", "+") else -1.0  # Bare variable name → coefficient 1
    else:
        coef = float(coef_str)
    coefficients[var] = coefficients.get(var, 0) + coef  # Accumulate same-variable coefficients
```

#### _parse_variable() — Variable parser

```python
def _parse_variable(name: str, spec: dict) -> VariableSpec:
    if spec.get("cat") == "binary":
        return VariableSpec(name=name, lb=0, ub=1, cat="binary")  # Auto-set bounds for binary
    return VariableSpec(
        name=name,
        lb=spec.get("lb", 0),         # Default lower bound: 0
        ub=spec.get("ub"),            # Default upper bound: None (unbounded)
        cat=spec.get("cat", "continuous"),
    )
```

Binary variables get special treatment — just write `{cat: binary}` in YAML without manually setting lb/ub.

#### _parse_constraint() — Constraint parser

```python
def _parse_constraint(raw: dict, index: int) -> ConstraintSpec:
    name = raw.get("name", f"c{index}")   # Auto-name if omitted: c0, c1, ...
    if "expression" in raw:
        coefficients, sense, rhs = _parse_expression(raw["expression"])  # Expression syntax
    else:
        coefficients = dict(raw["coefficients"])  # Structured syntax
        sense = raw["sense"]
        rhs = float(raw["rhs"])
    return ConstraintSpec(name=name, coefficients=coefficients, sense=sense, rhs=rhs)
```

Entry point for both YAML constraint syntaxes, producing a uniform ConstraintSpec.

#### load_config() — YAML loader

```python
def load_config(path: str) -> ProblemConfig:
    with open(path) as f:
        data = yaml.safe_load(f)                                  # Parse YAML → dict

    variables = {name: _parse_variable(name, spec)
                 for name, spec in data["variables"].items()}     # Parse each variable
    objective = ObjectiveSpec(coefficients=data["objective"]["coefficients"])
    constraints = [_parse_constraint(c, i)
                   for i, c in enumerate(data.get("constraints", []))]

    return ProblemConfig(
        name=data["name"],
        sense=data.get("sense", "minimize"),   # Default: minimize
        variables=variables,
        objective=objective,
        constraints=constraints,
    )
```

`yaml.safe_load` only parses standard YAML types — it won't execute arbitrary code, making it safer than `yaml.load`.

#### ILPSolver class — Main solver

**Initialization**

```python
class ILPSolver:
    def __init__(self, config_path: str):
        self.config = load_config(config_path)       # Load and parse YAML
        self._prob: pulp.LpProblem | None = None     # PuLP problem object (set after solve)
        self._vars: dict[str, pulp.LpVariable] = {}   # PuLP variable dict
        self.result: SolutionResult | None = None     # Solver result (set after solve)
```

**solve() — Core solve method**

```python
    def solve(self) -> SolutionResult:
        cfg = self.config
        sense = pulp.LpMinimize if cfg.sense == "minimize" else pulp.LpMaximize
        prob = pulp.LpProblem(cfg.name, sense)        # Create PuLP problem object
```

Sets optimization direction based on the YAML `sense` field.

```python
        # Create decision variables
        for name, spec in cfg.variables.items():
            self._vars[name] = pulp.LpVariable(
                name, lowBound=spec.lb, upBound=spec.ub, cat=_CAT_MAP[spec.cat]
            )
```

Converts each YAML variable to a PuLP LpVariable. `_CAT_MAP` translates strings to PuLP constants.

```python
        # Build objective function
        obj_expr = pulp.lpSum(
            coef * self._vars[var] for var, coef in cfg.objective.coefficients.items()
        )
        prob += obj_expr, "objective"
```

`pulp.lpSum` generates a linear expression `2*x + 3*y`, added to the problem via `+=`.

```python
        # Add constraints
        for c in cfg.constraints:
            lhs = pulp.lpSum(coef * self._vars[var] for var, coef in c.coefficients.items())
            prob += (lhs <= c.rhs if c.sense == "le"
                     else lhs >= c.rhs if c.sense == "ge"
                     else lhs == c.rhs), c.name
```

Each constraint builds a linear LHS expression, then forms `lhs <= rhs` based on sense.

```python
        self._prob = prob
        status = prob.solve()                          # Call CBC solver
```

`prob.solve()` internally: serializes to MPS file → launches CBC process → reads solution file.

```python
        # Extract sensitivity data
        duals, slacks = {}, {}
        if prob.constraints:
            for name, constraint in prob.constraints.items():
                duals[name] = constraint.pi            # Shadow price (dual value)
                slacks[name] = constraint.slack        # Slack
```

- `constraint.pi` — dual variable value (shadow price): how much the objective changes when RHS increases by 1
- `constraint.slack` — slack: 0 means binding (active at equality), positive means slack

```python
        self.result = SolutionResult(
            status=pulp.LpStatus[status],              # Numeric code → readable string
            objective_value=pulp.value(prob.objective),
            variables={name: v.varValue for name, v in self._vars.items()},
            duals=duals,
            slacks=slacks,
        )
        return self.result
```

`pulp.LpStatus` maps integer codes to strings (1→"Optimal", -1→"Infeasible", etc.).

**print_result() — Display results**

```python
    def print_result(self) -> None:
        r = self.result
        print(f"Problem: {self.config.name}")
        print(f"Status : {r.status}")                  # Optimal / Infeasible etc.
        print(f"Objective = {r.objective_value}")
        for name, val in r.variables.items():
            print(f"  {name} = {val}")                 # Each variable's value
```

**sensitivity_report() — Sensitivity analysis**

```python
    def sensitivity_report(self) -> str:
        lines = ["\nSensitivity Report", "-" * 40]
        if self.config.is_integer:
            lines.append("Note: dual values are from LP relaxation (not exact for ILP).")
```

Key point: for integer programs, dual values come from LP relaxation (relaxing integer/binary to continuous), so they're not exact.

```python
        lines.append(f"{'Constraint':<20} {'Slack':>10} {'Dual':>10}")
        for name in r.slacks:
            slack = r.slacks[name]
            dual = r.duals.get(name)
            dual_str = f"{dual:.4f}" if dual is not None else "N/A"
            lines.append(f"{name:<20} {slack:>10.4f} {dual_str:>10}")
```

Outputs a formatted table of slack and dual values per constraint.

#### main() — CLI entry point

```python
def main():
    parser = argparse.ArgumentParser(description="ILP Solver (PuLP + CBC)")
    parser.add_argument("config", help="Path to YAML problem definition")       # Required: YAML path
    parser.add_argument("--visualize", "-v", action="store_true", help="...")    # Optional: chart
    args = parser.parse_args()

    solver = ILPSolver(args.config)    # Load config
    solver.solve()                     # Solve
    solver.print_result()              # Print results
    print(solver.sensitivity_report()) # Sensitivity analysis

    if args.visualize:
        tmp_dir = Path("tmp")
        tmp_dir.mkdir(exist_ok=True)                           # Ensure tmp/ exists
        out = tmp_dir / (Path(args.config).stem + "_result.png")  # e.g. tmp/knapsack_result.png
        visualize(solver.config, solver.result, str(out))
```

---

### visualizer.py — Visualization

> Automatically selects visualization strategy based on variable count.

#### Entry function

```python
def visualize(cfg, result, output_path):
    var_names = list(cfg.variables.keys())
    if len(var_names) == 2:
        _plot_2d(cfg, result, var_names, output_path)     # 2 vars → feasible region
    else:
        _plot_summary(cfg, result, output_path)            # Otherwise → summary bars
```

#### _plot_2d() — 2-variable feasible region plot

```python
def _plot_2d(cfg, result, var_names, output_path):
    xname, yname = var_names
    xval = result.variables[xname] or 0                   # Optimal x
    yval = result.variables[yname] or 0                   # Optimal y

    bound = max(abs(xval), abs(yval), 10) * 2             # Plot range, ensures optimum is visible
    xs = np.linspace(0, bound, 500)                       # 500 sample points
```

**Draw constraint boundary lines**

```python
    for c in cfg.constraints:
        cx = c.coefficients.get(xname, 0)                 # Coefficient of x
        cy = c.coefficients.get(yname, 0)                 # Coefficient of y
        if cy == 0:                                       # Vertical line (e.g. x <= 5)
            xv = c.rhs / cx
            ax.axvline(xv, color="gray", linestyle="--", alpha=0.6)
            continue
        ys = (c.rhs - cx * xs) / cy                       # Solve cx*x + cy*y ~ rhs for y
        ax.plot(xs, ys, label=label)                       # Draw constraint line
```

Rearranges `cx*x + cy*y ~ rhs` into `y = (rhs - cx*x) / cy` to plot each constraint.

**Fill feasible region**

```python
    y_lower = np.full_like(xs, 0)                         # Initial bound: y >= 0
    for c in cfg.constraints:
        ys = (c.rhs - cx * xs) / cy
        if c.sense == "ge":
            y_lower = np.maximum(y_lower, ys)             # >= constraint raises lower bound
        else:
            y_lower = np.minimum(y_lower, ys)             # <= constraint lowers upper bound
    ax.fill_between(xs, y_lower, bound, alpha=0.15, color="green")
```

Tightens the feasible region boundary constraint by constraint, then fills.

**Mark optimal point**

```python
    ax.plot(xval, yval, "r*", markersize=15, label=f"optimal ({xval:.1f}, {yval:.1f})")
```

Red star marks the optimal solution.

#### _plot_summary() — General summary chart

```python
def _plot_summary(cfg, result, output_path):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))  # Two side-by-side subplots

    # Left: variable values
    names = list(result.variables.keys())
    vals = [result.variables[n] or 0 for n in names]
    ax1.bar(names, vals, color="steelblue")

    # Right: constraint slack
    cnames = list(result.slacks.keys())
    slacks = [result.slacks[n] for n in cnames]
    ax2.bar(cnames, slacks, color="coral")
    ax2.axhline(0, color="black", linewidth=0.5)           # Zero reference line
```

- Left chart: shows which variables are large vs. zero at a glance
- Right chart: slack indicates constraint activity — 0 = binding (active), positive = slack available

---

## Project Structure

```
lp-explorer/
├── models.py          # Data models (VariableSpec, ConstraintSpec, SolutionResult, etc.)
├── solver.py          # ILPSolver class: YAML parse → PuLP model → CBC solve → output
├── visualizer.py      # Visualization: 2D feasible region / summary bar charts
├── requirements.txt   # Python dependencies
├── examples/          # Example problem definitions
│   ├── knapsack.yaml  # 0-1 knapsack (structured constraint syntax)
│   └── production.yaml # Production plan (expression constraint syntax)
└── tmp/               # Generated outputs (gitignored)
```

## Solver Backend

Uses [CBC](https://github.com/coin-or/Cbc) (COIN-OR Branch and Cut) via PuLP. CBC is an open-source MILP solver included with PuLP — no additional installation required.

For continuous LP subproblems, CBC uses the **simplex method**. For integer/binary variables, it applies **branch and cut** with various cutting plane strategies (Gomory, knapsack covers, flow covers, etc.).

## CLI Reference

```
python solver.py <config.yaml> [-v, --visualize]
```

| Argument        | Description                         |
|-----------------|-------------------------------------|
| `config`        | Path to YAML problem definition     |
| `--visualize`   | Generate visualization to `tmp/`    |
