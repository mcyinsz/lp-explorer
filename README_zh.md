# LP Explorer

基于 PuLP + CBC 的统一整数线性规划（ILP）求解框架。用 YAML 文件定义优化问题，一条命令完成求解、灵敏度分析和可视化。

## 功能特性

- **YAML 定义问题** — 用简洁的配置文件描述变量、目标和约束
- **灵活的约束语法** — 支持可读表达式（`x + y >= 10`）和结构化字典两种写法
- **多种变量类型** — 连续变量、整数变量、0-1 变量
- **灵敏度分析** — 约束松弛量、影子价格（dual）、检验数（reduced cost）
- **6 种可视化图表** — 可行域、变量取值、资源利用率、目标贡献占比、约束矩阵热力图、约束间隙图
- **4 种 YAML 报告** — 求解摘要、变量详情、约束详情、目标分解

## 快速开始

### 安装依赖

```bash
pip install -r requirements.txt
```

### 运行示例

```bash
# 基础求解（仅文本输出）
python solver.py examples/knapsack.yaml

# 生成全部可视化
python solver.py examples/production.yaml -v

# 单独生成某种可视化
python solver.py examples/factory.yaml --visual-value
python solver.py examples/factory.yaml --visual-resource
python solver.py examples/factory.yaml --visual-objective
python solver.py examples/factory.yaml --visual-heatmap
python solver.py examples/factory.yaml --visual-gap
python solver.py examples/production.yaml --visual-region

# YAML 数据报告
python solver.py examples/factory.yaml --report-solution
python solver.py examples/factory.yaml --report-variable --report-constraint
python solver.py examples/factory.yaml --report-objective

# 自由组合
python solver.py examples/factory.yaml --visual-resource --visual-objective --report-solution
```

### Python API

```python
from solver import ILPSolver

solver = ILPSolver("examples/knapsack.yaml")
result = solver.solve()
solver.print_result()
print(solver.sensitivity_report())
```

## YAML 格式说明

```yaml
name: my_problem
sense: minimize            # minimize 或 maximize

variables:
  x: { lb: 0, ub: 10, cat: integer }    # cat: continuous / integer / binary
  y: { lb: 0, cat: continuous }
  z: { cat: binary }                     # binary 简写

objective:
  coefficients: { x: 2, y: 3, z: 1 }

constraints:
  # 表达式写法
  - name: demand
    expression: x + y >= 10

  # 结构化写法
  - name: capacity
    coefficients: { x: 2, y: 1 }
    sense: le               # le (<=), ge (>=), eq (==)
    rhs: 14
```

### 变量字段说明

| 字段   | 默认值        | 说明                         |
|--------|--------------|------------------------------|
| `lb`   | `0`          | 下界                         |
| `ub`   | `None`       | 上界（省略则无上界）          |
| `cat`  | `continuous` | `continuous`、`integer` 或 `binary` |

`cat: binary` 时自动设置 `lb=0, ub=1`。

### 配置校验与结果语义

- 非法配置会在求解前直接抛出清晰的 `ValueError`。
- 问题 `sense` 只能是 `minimize` 或 `maximize`。
- 约束 `sense` 只能是 `le`、`ge` 或 `eq`。
- 目标函数和约束里引用的变量必须先在 `variables` 中声明。
- 所有报告都以机器可读的 YAML 输出。
- 对 `Infeasible`、`Unbounded` 等非最优状态，依赖有效原始解的字段会序列化为 `null`。

---

## 代码详解

### 整体架构

```
YAML 文件
   │
   ▼
load_config() ── 解析 YAML → ProblemConfig 数据对象
   │
   ▼
ILPSolver.solve() ── 将 ProblemConfig 翻译成 PuLP 模型 → 调用 CBC 求解
   │
   ▼
SolutionResult ── 求解结果（变量值、目标值、slack、dual）
   │
   ▼
visualize() ── 根据变量数量选择 2D 可行域图或摘要柱状图
```

三个模块各司其职：
- **models.py** — 纯数据结构，不涉及任何求解逻辑
- **solver.py** — 核心求解逻辑：解析输入 → 建模 → 求解 → 输出
- **visualizer.py** — 根据求解结果生成图表

---

### models.py — 数据模型

> 定义了问题配置和求解结果的纯数据结构，与求解器完全解耦。

#### VariableSpec — 变量规格

```python
@dataclass
class VariableSpec:
    name: str                              # 变量名，如 "x", "y1"
    lb: float = 0                          # 下界，默认 0（即 x >= 0）
    ub: Optional[float] = None             # 上界，None 表示无上界
    cat: str = "continuous"                # 变量类型：continuous / integer / binary
```

对应 YAML 中的：

```yaml
variables:
  x: { lb: 0, ub: 10, cat: integer }
```

`lb=0` 是运筹学中最常见的设定（产量、数量不能为负），所以设为默认值。

#### ConstraintSpec — 约束规格

```python
@dataclass
class ConstraintSpec:
    name: str                              # 约束名，用于输出和调试
    coefficients: dict[str, float]         # 变量系数，如 {"x": 2, "y": 1}
    sense: str                             # "le"(<=) / "ge"(>=) / "eq"(==)
    rhs: float                             # 右端项，如 14
```

表示数学上的 `2x + 1y <= 14`。无论 YAML 中用表达式写法还是结构化写法，最终都统一转成这个格式。

#### ObjectiveSpec — 目标函数

```python
@dataclass
class ObjectiveSpec:
    coefficients: dict[str, float]         # 目标函数系数，如 {"x": 2, "y": 3}
```

表示 `min 2x + 3y`。最大值/最小值由 ProblemConfig 的 `sense` 字段控制。

#### ProblemConfig — 完整问题配置

```python
@dataclass
class ProblemConfig:
    name: str                              # 问题名称
    sense: str                             # "minimize" / "maximize"
    variables: dict[str, VariableSpec]     # 所有变量
    objective: ObjectiveSpec               # 目标函数
    constraints: list[ConstraintSpec]      # 所有约束

    @property
    def is_integer(self) -> bool:
        return any(v.cat in ("integer", "binary") for v in self.variables.values())
```

`is_integer` 属性用于灵敏度报告中判断是否需要提示"dual 值来自 LP 松弛"。

#### SolutionResult — 求解结果

```python
@dataclass
class SolutionResult:
    status: str                            # "Optimal" / "Infeasible" / "Unbounded" 等
    objective_value: Optional[float]       # 最优目标值，无解时为 None
    variables: dict[str, Optional[float]]  # 各变量取值，如 {"x": 10.0, "y": 0.0}
    duals: dict[str, Optional[float]]      # 各约束的影子价格（对偶值）
    slacks: dict[str, float]               # 各约束的松弛量
    reduced_costs: dict[str, Optional[float]]  # 各变量的检验数（reduced cost）
```

- `slacks["demand"] = -0.0` 表示该约束刚好取等（binding constraint）
- `duals["demand"] = 2.0` 表示需求约束放松 1 单位，目标值变化 2（影子价格）
- `reduced_costs["x"] = 0` 表示变量在最优基中；非零值表示变量在边界上
- 对于 ILP 问题，`duals` 和 `reduced_costs` 来自 LP 松弛解，不是精确值

---

### solver.py — 求解器核心

> 负责将 YAML 配置翻译成 PuLP 模型并调用 CBC 求解。

#### 全局映射表

```python
_OP_MAP = {">=": "ge", "<=": "le", "==": "eq"}          # 比较运算符 → 内部 sense 标识
_CAT_MAP = {"continuous": pulp.LpContinuous,             # YAML cat 字符串 → PuLP 常量
            "integer": pulp.LpInteger,
            "binary": pulp.LpBinary}
```

#### _parse_expression() — 表达式解析器

```python
def _parse_expression(expr_str: str) -> tuple[dict[str, float], str, float]:
```

将 `"2*x + y >= 10"` 这样的字符串解析为 `({"x": 2, "y": 1}, "ge", 10)`。

**第一步：定位比较运算符，并拆分左右两边**

```python
cmp_match = re.search(r"(>=|<=|==)", expr_str)
op = cmp_match.group(1)                           # ">="
left_str = expr_str[: cmp_match.start()].strip() # "2*x + y"
right_str = expr_str[cmp_match.end() :].strip()  # "10"
```

解析器先找到比较运算符，再分别解析左右两边。

**第二步：分别提取左右两边的系数与常数**

```python
for match in re.finditer(r"([+-]?\s*\d*\.?\d*)\s*\*\s*([A-Za-z_]\w*)", s):
    coef_str, var = match.group(1).replace(" ", ""), match.group(2)
```

第一轮匹配带显式系数的项，例如：
- `"2*x"` → coef=2, var=x
- `"+3.5*y"` → coef=3.5, var=y

裸变量会在第二轮单独处理：

```python
for match in re.finditer(r"(?:^|(?<=[-+\s]))([+-]?)\s*([A-Za-z_]\w*)(?!\s*\*|\d)", s):
    var = match.group(2)
    sign = match.group(1)
    coeffs[var] = coeffs.get(var, 0) + (-1.0 if sign == "-" else 1.0)
```

例如：
- `"-x"` → coef=-1, var=x
- `"x"` → coef=1, var=x
- `"x + x"` → 累加后 coef=2

```python
    coef = float(coef_str) if coef_str and coef_str not in ("+", "-") else (1.0 if coef_str != "-" else -1.0)
    coeffs[var] = coeffs.get(var, 0) + coef
```

如果变量出现在右边，它会被移到左边并取相反数；常数项会并入最终的 `rhs`。

#### _parse_variable() — 变量解析

```python
def _parse_variable(name: str, spec: dict) -> VariableSpec:
    cat = spec.get("cat", "continuous")
    if cat not in _CAT_MAP:
        raise ValueError(...)
    if cat == "binary":
        return VariableSpec(name=name, lb=0, ub=1, cat="binary")  # binary 自动设界
    return VariableSpec(
        name=name,
        lb=spec.get("lb", 0),         # 默认下界 0
        ub=spec.get("ub"),            # 默认无上界
        cat=cat,
    )
```

对 binary 变量做了特殊处理，同时会提前拒绝非法变量类型。

#### _parse_constraint() — 约束解析

```python
def _parse_constraint(raw: dict, index: int) -> ConstraintSpec:
    name = raw.get("name", f"c{index}")   # 没命名则自动编号 c0, c1, ...
    if "expression" in raw:
        coefficients, sense, rhs = _parse_expression(raw["expression"])  # 表达式写法
    else:
        coefficients = dict(raw["coefficients"])  # 结构化写法
        sense = raw["sense"]
        rhs = float(raw["rhs"])
    if sense not in {"le", "ge", "eq"}:
        raise ValueError(...)
    return ConstraintSpec(name=name, coefficients=coefficients, sense=sense, rhs=rhs)
```

支持两种 YAML 约束写法的入口，统一输出 ConstraintSpec。

#### load_config() — YAML 加载

```python
def load_config(path: str) -> ProblemConfig:
    with open(path) as f:
        data = yaml.safe_load(f)                                  # 解析 YAML 为 dict

    sense = data.get("sense", "minimize")
    if sense not in {"minimize", "maximize"}:
        raise ValueError(...)

    variables = {name: _parse_variable(name, spec)
                 for name, spec in data["variables"].items()}     # 逐个解析变量
    objective = ObjectiveSpec(coefficients=data["objective"]["coefficients"])
    constraints = [_parse_constraint(c, i)
                   for i, c in enumerate(data.get("constraints", []))]

    known_vars = set(variables)
    if set(objective.coefficients) - known_vars:
        raise ValueError(...)
    for constraint in constraints:
        if set(constraint.coefficients) - known_vars:
            raise ValueError(...)

    return ProblemConfig(
        name=data["name"],
        sense=sense,
        variables=variables,
        objective=objective,
        constraints=constraints,
    )
```

`yaml.safe_load` 只解析标准 YAML 类型，而且 `load_config()` 还会在求解前检查枚举值和变量引用是否合法。

#### ILPSolver 类 — 求解器主体

**初始化**

```python
class ILPSolver:
    def __init__(self, config_path: str):
        self.config = load_config(config_path)       # 加载并解析 YAML
        self._prob: pulp.LpProblem | None = None     # PuLP 问题对象（求解后才有）
        self._vars: dict[str, pulp.LpVariable] = {}   # PuLP 变量字典
        self.result: SolutionResult | None = None     # 求解结果（求解后才有）
```

**solve() — 核心求解方法**

```python
    def solve(self) -> SolutionResult:
        cfg = self.config
        sense = pulp.LpMinimize if cfg.sense == "minimize" else pulp.LpMaximize
        prob = pulp.LpProblem(cfg.name, sense)        # 创建 PuLP 问题对象
```

根据 YAML 中的 `sense` 字段决定优化方向。

```python
        # 创建决策变量
        for name, spec in cfg.variables.items():
            self._vars[name] = pulp.LpVariable(
                name, lowBound=spec.lb, upBound=spec.ub, cat=_CAT_MAP[spec.cat]
            )
```

遍历 YAML 中定义的每个变量，转成 PuLP 的 LpVariable。`_CAT_MAP` 把字符串转成 PuLP 内部常量。

```python
        # 构建目标函数
        obj_expr = pulp.lpSum(
            coef * self._vars[var] for var, coef in cfg.objective.coefficients.items()
        )
        prob += obj_expr, "objective"
```

`pulp.lpSum` 生成线性表达式 `2*x + 3*y`，然后用 `+=` 添加到问题中。

```python
        # 添加约束
        for c in cfg.constraints:
            lhs = pulp.lpSum(coef * self._vars[var] for var, coef in c.coefficients.items())
            prob += (lhs <= c.rhs if c.sense == "le"
                     else lhs >= c.rhs if c.sense == "ge"
                     else lhs == c.rhs), c.name
```

每条约束先生成左端线性表达式，再根据 sense 拼接成 `lhs <= rhs` 形式，用 `+=` 添加。

```python
        self._prob = prob
        status = prob.solve()                          # 调用 CBC 求解
```

`prob.solve()` 内部流程：序列化为 MPS 文件 → 启动 CBC 进程 → 读取解文件。

```python
        # 提取灵敏度分析数据
        duals, slacks = {}, {}
        if prob.constraints:
            for name, constraint in prob.constraints.items():
                duals[name] = constraint.pi            # 影子价格（对偶值）
                slacks[name] = constraint.slack        # 松弛量

        reduced_costs = {name: v.dj for name, v in self._vars.items()}
```

- `constraint.pi` — 对偶变量值（影子价格），表示约束右端项增加 1 单位时目标值的变化量
- `constraint.slack` — 松弛量，0 表示约束取等（binding），正值表示有余量
- `v.dj` — 检验数（reduced cost），表示非基变量的目标系数需要改善多少才能入基

```python
        status_str = pulp.LpStatus[status]
        has_solution = status_str == "Optimal"
        self.result = SolutionResult(
            status=status_str,                         # 数字状态码 → 可读字符串
            objective_value=pulp.value(prob.objective) if has_solution else None,
            variables={name: (v.varValue if has_solution else None) for name, v in self._vars.items()},
            duals=duals if has_solution else {},
            slacks=slacks if has_solution else {},
            reduced_costs=reduced_costs if has_solution else {},
        )
        return self.result
```

`pulp.LpStatus` 把整数状态码映射为字符串（1→"Optimal", -1→"Infeasible" 等）。只有 `Optimal` 状态才暴露原始解和灵敏度字段；其他状态在 YAML 报告中会写成 `null`。

**print_result() — 打印结果**

```python
    def print_result(self) -> None:
        r = self.result
        print(f"Problem: {self.config.name}")
        print(f"Status : {r.status}")                  # Optimal / Infeasible 等
        print(f"Objective = {r.objective_value}")
        for name, val in r.variables.items():
            print(f"  {name} = {val}")                 # 每个变量的取值
```

**sensitivity_report() — 灵敏度报告**

```python
    def sensitivity_report(self) -> str:
        lines = ["\nSensitivity Report", "-" * 40]
        if self.config.is_integer:
            lines.append("Note: dual values are from LP relaxation (not exact for ILP).")
```

关键点：整数规划的对偶值来自 LP 松弛（把 integer/binary 变量放松为 continuous），不是精确的，需要提醒用户。

```python
        lines.append(f"{'Constraint':<20} {'Slack':>10} {'Dual':>10}")
        for name in r.slacks:
            slack = r.slacks[name]
            dual = r.duals.get(name)
            dual_str = f"{dual:.4f}" if dual is not None else "N/A"
            lines.append(f"{name:<20} {slack:>10.4f} {dual_str:>10}")
```

以表格形式输出每个约束的松弛量和对偶值。

#### main() — 命令行入口

```python
def main():
    parser = argparse.ArgumentParser(description="ILP Solver (PuLP + CBC)")
    parser.add_argument("config", help="Path to YAML problem definition")       # 必填：YAML 路径
    parser.add_argument("--visualize", "-v", action="store_true", help="...")    # 可选：生成图
    args = parser.parse_args()

    solver = ILPSolver(args.config)    # 加载配置
    solver.solve()                     # 求解
    solver.print_result()              # 打印结果
    print(solver.sensitivity_report()) # 灵敏度分析

    if args.visualize:
        tmp_dir = Path("tmp")
        tmp_dir.mkdir(exist_ok=True)                           # 确保 tmp/ 存在
        out = tmp_dir / (Path(args.config).stem + "_result.png")  # 如 tmp/knapsack_result.png
        visualize(solver.config, solver.result, str(out))
```

---

### visualizer.py — 可视化

> 根据变量数量自动选择可视化策略。

#### 入口函数

```python
def visualize(cfg: ProblemConfig, result: SolutionResult, output_path: str) -> None:
    var_names = list(cfg.variables.keys())
    if len(var_names) == 2:
        _plot_2d(cfg, result, var_names, output_path)     # 2 变量 → 可行域图
    else:
        _plot_summary(cfg, result, output_path)            # 其他 → 摘要柱状图
```

#### _plot_2d() — 2 变量可行域图

```python
def _plot_2d(cfg, result, var_names, output_path):
    xname, yname = var_names
    xval = result.variables[xname] or 0                   # 最优解的 x 值
    yval = result.variables[yname] or 0                   # 最优解的 y 值

    bound = max(abs(xval), abs(yval), 10) * 2             # 画图范围，确保最优解在图内
    xs = np.linspace(0, bound, 500)                       # 500 个采样点
```

**画约束边界线**

```python
    for c in cfg.constraints:
        cx = c.coefficients.get(xname, 0)                 # x 的系数
        cy = c.coefficients.get(yname, 0)                 # y 的系数
        if cy == 0:                                       # 竖直线（如 x <= 5）
            xv = c.rhs / cx
            ax.axvline(xv, color="gray", linestyle="--", alpha=0.6)
            continue
        ys = (c.rhs - cx * xs) / cy                       # 从 cx*x + cy*y ~ rhs 解出 y
        ax.plot(xs, ys, label=label)                       # 画约束线
```

将 `cx*x + cy*y ~ rhs` 变形为 `y = (rhs - cx*x) / cy`，画出每条约束线。

**填充可行域**

```python
    y_lower = np.full_like(xs, 0)                         # 初始下界：y >= 0
    for c in cfg.constraints:
        ys = (c.rhs - cx * xs) / cy
        if c.sense == "ge":
            y_lower = np.maximum(y_lower, ys)             # >= 约束取上界
        else:
            y_lower = np.minimum(y_lower, ys)             # <= 约束取下界
    ax.fill_between(xs, y_lower, bound, alpha=0.15, color="green")
```

逐个约束收紧可行域边界，`fill_between` 填充可行区域。

**标记最优解**

```python
    ax.plot(xval, yval, "r*", markersize=15, label=f"optimal ({xval:.1f}, {yval:.1f})")
```

红色五角星标记最优解的位置。

#### _plot_summary() — 通用摘要图

```python
def _plot_summary(cfg, result, output_path):
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))  # 左右两张子图

    # 左图：变量取值柱状图
    names = list(result.variables.keys())
    vals = [result.variables[n] or 0 for n in names]
    ax1.bar(names, vals, color="steelblue")

    # 右图：约束松弛量柱状图
    cnames = list(result.slacks.keys())
    slacks = [result.slacks[n] for n in cnames]
    ax2.bar(cnames, slacks, color="coral")
    ax2.axhline(0, color="black", linewidth=0.5)           # 零线参考
```

- 左图一眼看出哪些变量取值大、哪些为零
- 右图的 slack 表示约束余量：0 = 紧约束（binding），正数 = 有余量

---

## 验证

一键验证所有示例是否与已知最优解吻合：

```bash
python validate.py           # 返回退出码 0（通过）或 1（失败）
python validate.py 2>/dev/null  # 静默 CBC 求解器输出
```

## 项目结构

```
lp-explorer/
├── models.py          # 数据模型（VariableSpec, ConstraintSpec, SolutionResult 等）
├── solver.py          # ILPSolver 类：YAML 解析 → PuLP 建模 → CBC 求解 → 结果输出
├── visualizer.py      # 6 种图表 + 向后兼容的自动检测
├── validate.py        # 一键验证脚本，校验全部示例与已知最优解
├── requirements.txt   # Python 依赖
├── examples/          # 示例问题定义
│   ├── knapsack.yaml              # 0-1 背包问题（结构化约束写法）
│   ├── production.yaml            # 生产计划问题（表达式约束写法）
│   ├── factory.yaml               # 多产品工厂排产（4 变量、4 约束）
│   ├── diet.yaml                  # 饮食问题（最小化成本，LP 松弛）
│   ├── classic_production.yaml    # 经典 2 变量 LP 最大化
│   ├── classic_diet.yaml          # 经典 2 变量饮食最小化
│   ├── binary_knapsack_3item.yaml # 3 物品 0-1 背包
│   ├── set_cover.yaml             # 集合覆盖（4 个二值变量）
│   ├── facility_location.yaml     # 无容量设施选址（MILP）
│   ├── assignment.yaml            # 3×3 指派问题
│   ├── unbounded.yaml             # 无界 LP
│   └── infeasible.yaml            # 不可行 LP
└── tmp/               # 生成产物（已 gitignore）
```

## 求解器后端

使用 PuLP 自带的 [CBC](https://github.com/coin-or/Cbc)（COIN-OR Branch and Cut）求解器，无需额外安装。

- 连续 LP 子问题：使用**单纯形法**
- 整数/0-1 变量：使用**分支割平面法**，包含 Gomory 割、背包覆盖、流覆盖等多种割平面策略

## 命令行参数

```
python solver.py <config.yaml> [选项]
```

### 可视化选项

所有图表输出到 `tmp/` 目录。

| 参数                  | 输出文件              | 说明                                   |
|----------------------|----------------------|----------------------------------------|
| `-v`, `--visualize`  | `*_result.png`       | 默认可视化（2 变量→可行域，其他→摘要）    |
| `--visual-region`    | `*_region.png`       | 2D 可行域 + 等值线 + 最优解标记          |
| `--visual-value`     | `*_value.png`        | 变量取值柱状图                           |
| `--visual-resource`  | `*_resource.png`     | 资源利用率堆叠柱状图 + RHS 上限线        |
| `--visual-objective` | `*_objective.png`    | 目标贡献占比饼图                         |
| `--visual-heatmap`   | `*_heatmap.png`      | 约束系数矩阵热力图                       |
| `--visual-gap`       | `*_gap.png`          | 约束间隙图，对比每条约束的 LHS 与 RHS    |

### 报告选项

所有报告输出为 YAML 格式到 `tmp/` 目录。

| 参数                   | 输出文件              | 说明                                    |
|-----------------------|----------------------|-----------------------------------------|
| `--report-solution`   | `*_solution.yaml`    | 求解状态、目标值、变量值                  |
| `--report-variable`   | `*_variable.yaml`    | 变量值、检验数、上下界、类型               |
| `--report-constraint` | `*_constraint.yaml`  | 约束 LHS、RHS、slack、dual、是否 binding  |
| `--report-objective`  | `*_objective.yaml`   | 各变量贡献（值 × 系数）及百分比            |

对于 `Infeasible` 和 `Unbounded` 等非最优模型，依赖有效原始解的报告字段会序列化为 `null`。

### YAML 报告示例

`--report-solution` → `tmp/factory_solution.yaml`:
```yaml
status: Optimal
objective: 31540.0
variables:
  A: 87.0
  B: 34.0
  C: 0.0
  D: 80.0
```

`--report-constraint` → `tmp/factory_constraint.yaml`:
```yaml
constraints:
  - name: steel
    lhs: 598.0
    rhs: 600.0
    slack: 2.0
    dual: 0.0
    binding: false
  - name: labor
    lhs: 800.0
    rhs: 800.0
    slack: 0.0
    dual: 42.5
    binding: true
```

`--report-objective` → `tmp/factory_objective.yaml`:
```yaml
total: 31540.0
contributions:
  - variable: A
    coefficient: 120
    value: 87.0
    contribution: 10440.0
    percentage: 33.1
  - variable: D
    coefficient: 200
    value: 80.0
    contribution: 16000.0
    percentage: 50.7
```

非最优状态示例（`examples/infeasible.yaml`）：

```yaml
status: Infeasible
objective: null
variables:
  x: null
```

凡是依赖有效原始解的字段，都会在模型达到 `Optimal` 之前保持为 `null`。
