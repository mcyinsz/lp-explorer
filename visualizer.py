import numpy as np
import matplotlib.pyplot as plt

from models import ProblemConfig, SolutionResult


def visualize(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    var_names = list(cfg.variables.keys())
    if len(var_names) == 2:
        _plot_2d(cfg, result, var_names, output_path)
    else:
        _plot_summary(cfg, result, output_path)


def _sense_to_op(sense: str) -> str:
    return {"le": "<=", "ge": ">=", "eq": "=="}[sense]


def _plot_2d(cfg: ProblemConfig, result: SolutionResult, var_names: list[str], output_path: str) -> None:
    xname, yname = var_names
    xval = result.variables[xname] or 0
    yval = result.variables[yname] or 0

    fig, ax = plt.subplots(figsize=(8, 6))

    bound = max(abs(xval), abs(yval), 10) * 2
    xs = np.linspace(0, bound, 500)

    for c in cfg.constraints:
        # y expressed from: cx*x + cy*y ~ rhs => y = (rhs - cx*x) / cy
        cx = c.coefficients.get(xname, 0)
        cy = c.coefficients.get(yname, 0)
        if cy == 0:
            xv = c.rhs / cx if cx != 0 else 0
            if c.sense == "ge":
                ax.axvline(xv, color="gray", linestyle="--", alpha=0.6)
            else:
                ax.axvline(xv, color="gray", linestyle="--", alpha=0.6)
            continue
        ys = (c.rhs - cx * xs) / cy
        label = f"{_expr_str(c)} {_sense_to_op(c.sense)} {c.rhs}"
        ax.plot(xs, ys, label=label)

    # Fill feasible region (approximate)
    y_lower = np.full_like(xs, 0)
    for c in cfg.constraints:
        cx = c.coefficients.get(xname, 0)
        cy = c.coefficients.get(yname, 0)
        if cy == 0:
            continue
        ys = (c.rhs - cx * xs) / cy
        if c.sense == "ge":
            y_lower = np.maximum(y_lower, ys)
        else:
            y_lower = np.minimum(y_lower, ys)
    ax.fill_between(xs, y_lower, bound, alpha=0.15, color="green", label="feasible region")

    # Optimal point
    ax.plot(xval, yval, "r*", markersize=15, label=f"optimal ({xval:.1f}, {yval:.1f})")

    ax.set_xlim(0, bound)
    ax.set_ylim(0, bound)
    ax.set_xlabel(xname)
    ax.set_ylabel(yname)
    ax.legend(fontsize=8)
    ax.set_title(f"{cfg.name} — Feasible Region & Optimal Solution")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


def _expr_str(c) -> str:
    parts = []
    for var, coef in c.coefficients.items():
        if coef == 1:
            parts.append(var)
        elif coef == -1:
            parts.append(f"-{var}")
        else:
            parts.append(f"{coef:g}*{var}")
    return " + ".join(parts).replace("+ -", "- ")


def _plot_summary(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    # Variable values
    names = list(result.variables.keys())
    vals = [result.variables[n] or 0 for n in names]
    ax1.bar(names, vals, color="steelblue")
    ax1.set_title("Variable Values")
    ax1.set_ylabel("Value")
    ax1.tick_params(axis="x", rotation=45)

    # Constraint slack
    if result.slacks:
        cnames = list(result.slacks.keys())
        slacks = [result.slacks[n] for n in cnames]
        ax2.bar(cnames, slacks, color="coral")
        ax2.set_title("Constraint Slack")
        ax2.set_ylabel("Slack")
        ax2.axhline(0, color="black", linewidth=0.5)
        ax2.tick_params(axis="x", rotation=45)

    fig.suptitle(f"{cfg.name} — Solution Summary")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)
