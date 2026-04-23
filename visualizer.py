import numpy as np
import matplotlib.pyplot as plt

from models import ProblemConfig, SolutionResult


# ============================================================
# 通用工具
# ============================================================

def _sense_to_op(sense: str) -> str:
    return {"le": "<=", "ge": ">=", "eq": "=="}[sense]


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


# ============================================================
# 2D 可行域图
# ============================================================

def plot_2d_region(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    var_names = list(cfg.variables.keys())
    if len(var_names) != 2:
        return
    xname, yname = var_names
    xval = result.variables[xname] or 0
    yval = result.variables[yname] or 0

    fig, ax = plt.subplots(figsize=(8, 6))
    bound = max(abs(xval), abs(yval), 10) * 2
    xs = np.linspace(0, bound, 500)

    for c in cfg.constraints:
        cx = c.coefficients.get(xname, 0)
        cy = c.coefficients.get(yname, 0)
        if cy == 0:
            xv = c.rhs / cx if cx != 0 else 0
            ax.axvline(xv, color="gray", linestyle="--", alpha=0.6)
            continue
        ys = (c.rhs - cx * xs) / cy
        label = f"{_expr_str(c)} {_sense_to_op(c.sense)} {c.rhs}"
        ax.plot(xs, ys, label=label)

    y_min = np.full_like(xs, 0.0)
    y_max = np.full_like(xs, bound, dtype=float)
    for c in cfg.constraints:
        cx = c.coefficients.get(xname, 0)
        cy = c.coefficients.get(yname, 0)
        if cy == 0:
            continue
        ys = (c.rhs - cx * xs) / cy
        if c.sense == "ge":
            y_min = np.maximum(y_min, ys)
        elif c.sense == "le":
            y_max = np.minimum(y_max, ys)
        else:
            y_min = np.maximum(y_min, ys)
            y_max = np.minimum(y_max, ys)
    feasible_mask = y_max >= y_min
    ax.fill_between(
        xs,
        y_min,
        y_max,
        where=feasible_mask,
        alpha=0.15,
        color="green",
        label="feasible region",
    )

    # Iso-profit lines
    cx_obj = cfg.objective.coefficients.get(xname, 0)
    cy_obj = cfg.objective.coefficients.get(yname, 0)
    if cy_obj != 0:
        for level in np.linspace(0, result.objective_value or bound, 5):
            ys_iso = (level - cx_obj * xs) / cy_obj
            ax.plot(xs, ys_iso, "k:", alpha=0.3, linewidth=0.8)

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


# ============================================================
# 变量取值柱状图
# ============================================================

def plot_variable_values(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    names = list(result.variables.keys())
    vals = [result.variables[n] or 0 for n in names]

    fig, ax = plt.subplots(figsize=(max(6, len(names) * 1.2), 5))
    bars = ax.bar(names, vals, color="steelblue")
    for bar, val in zip(bars, vals):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height(),
                f"{val:.1f}", ha="center", va="bottom", fontsize=9)
    ax.set_title(f"{cfg.name} — Variable Values")
    ax.set_ylabel("Value")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


# ============================================================
# 资源利用率堆叠柱状图
# ============================================================

def plot_resource_utilization(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    # Only show <= constraints (resource consumption), skip >= and ==
    resource_constraints = [c for c in cfg.constraints if c.sense == "le"]
    if not resource_constraints:
        return
    var_names = list(cfg.variables.keys())
    cnames = [c.name for c in resource_constraints]

    fig, ax = plt.subplots(figsize=(max(6, len(cnames) * 2), 6))
    bottoms = np.zeros(len(cnames))

    for var in var_names:
        val = result.variables[var] or 0
        usage = [c.coefficients.get(var, 0) * val for c in resource_constraints]
        ax.bar(cnames, usage, bottom=bottoms, label=var)
        bottoms += np.array(usage)

    # RHS line
    rhs_vals = [c.rhs for c in resource_constraints]
    ax.plot(cnames, rhs_vals, "r--", marker="o", label="RHS limit")

    ax.set_title(f"{cfg.name} — Resource Utilization")
    ax.set_ylabel("Usage")
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


# ============================================================
# 目标贡献饼图
# ============================================================

def plot_objective_breakdown(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    contributions = {}
    for var, coef in cfg.objective.coefficients.items():
        val = result.variables.get(var) or 0
        c = coef * val
        if abs(c) > 1e-9:
            contributions[var] = abs(c)

    if not contributions:
        return

    fig, ax = plt.subplots(figsize=(7, 7))
    labels = list(contributions.keys())
    sizes = list(contributions.values())
    total = sum(sizes)
    ax.pie(sizes, labels=labels, autopct=lambda p: f"{p:.1f}%", startangle=90)
    ax.set_title(f"{cfg.name} — Objective Contribution (total={total:.1f})")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


# ============================================================
# 约束矩阵热力图
# ============================================================

def plot_constraint_heatmap(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    var_names = list(cfg.variables.keys())
    cnames = [c.name for c in cfg.constraints]
    matrix = np.array([[c.coefficients.get(v, 0) for v in var_names] for c in cfg.constraints])

    fig, ax = plt.subplots(figsize=(max(6, len(var_names) * 1.2), max(4, len(cnames) * 0.8)))
    im = ax.imshow(matrix, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(range(len(var_names)))
    ax.set_xticklabels(var_names)
    ax.set_yticks(range(len(cnames)))
    ax.set_yticklabels(cnames)

    for i in range(len(cnames)):
        for j in range(len(var_names)):
            val = matrix[i, j]
            if val != 0:
                ax.text(j, i, f"{val:g}", ha="center", va="center", fontsize=9)

    fig.colorbar(im, ax=ax, label="Coefficient")
    ax.set_title(f"{cfg.name} — Constraint Matrix")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


# ============================================================
# 约束间隙图：LHS vs RHS + slack/surplus
# ============================================================

def plot_constraint_gap(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    if not cfg.constraints:
        return

    _SENSE_LABEL = {"le": "max (<=)", "ge": "min (>=)", "eq": "eq (==)"}

    cnames = []
    lhs_vals = []
    rhs_vals = []
    gaps = []
    colors = []
    sense_labels = []

    for c in cfg.constraints:
        lhs = sum(c.coefficients.get(v, 0) * (result.variables.get(v) or 0) for v in result.variables)
        rhs = c.rhs
        slack = result.slacks.get(c.name, 0)
        gap = abs(slack)
        binding = abs(slack) < 1e-9

        sense_labels.append(_SENSE_LABEL[c.sense])
        cnames.append(c.name)
        lhs_vals.append(lhs)
        rhs_vals.append(rhs)
        gaps.append(gap)
        colors.append("#e74c3c" if binding else "#2ecc71")

    fig, ax = plt.subplots(figsize=(max(7, len(cnames) * 1.5), 6))
    y_pos = np.arange(len(cnames))
    bar_height = 0.35

    # LHS bars
    bars_lhs = ax.barh(y_pos + bar_height / 2, lhs_vals, bar_height,
                       label="LHS (actual)", color="steelblue", edgecolor="white")
    # RHS bars
    bars_rhs = ax.barh(y_pos - bar_height / 2, rhs_vals, bar_height,
                       label="RHS (limit)", color="coral", edgecolor="white", alpha=0.7)

    # Annotate gap and binding status
    for i, (lhs, rhs, gap, color) in enumerate(zip(lhs_vals, rhs_vals, gaps, colors)):
        max_val = max(lhs, rhs)
        label = "binding" if abs(gap) < 1e-9 else f"gap={gap:.1f}"
        ax.text(max_val + max(max_val * 0.03, 1), y_pos[i], label,
                va="center", fontsize=9, color=color, fontweight="bold")

    ax.set_yticks(y_pos)
    ax.set_yticklabels([f"{n}  [{s}]" for n, s in zip(cnames, sense_labels)])
    ax.set_xlabel("Value")
    ax.set_title(f"{cfg.name} — Constraint Gap (LHS vs RHS)")
    ax.legend(loc="lower right")
    fig.tight_layout()
    fig.savefig(output_path, dpi=150)
    plt.close(fig)


# ============================================================
# 通用摘要图（向后兼容）
# ============================================================

def _plot_summary(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))

    names = list(result.variables.keys())
    vals = [result.variables[n] or 0 for n in names]
    ax1.bar(names, vals, color="steelblue")
    ax1.set_title("Variable Values")
    ax1.set_ylabel("Value")
    ax1.tick_params(axis="x", rotation=45)

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


# ============================================================
# 入口（向后兼容 -v 快捷方式）
# ============================================================

def visualize(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    var_names = list(cfg.variables.keys())
    if len(var_names) == 2:
        plot_2d_region(cfg, result, output_path)
    else:
        _plot_summary(cfg, result, output_path)
