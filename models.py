from dataclasses import dataclass, field
from typing import Optional


@dataclass
class VariableSpec:
    name: str
    lb: float = 0
    ub: Optional[float] = None
    cat: str = "continuous"  # continuous / integer / binary


@dataclass
class ConstraintSpec:
    name: str
    coefficients: dict[str, float]
    sense: str  # "le" (<=), "ge" (>=), "eq" (==)
    rhs: float


@dataclass
class ObjectiveSpec:
    coefficients: dict[str, float]


@dataclass
class ProblemConfig:
    name: str
    sense: str  # "minimize" / "maximize"
    variables: dict[str, VariableSpec]
    objective: ObjectiveSpec
    constraints: list[ConstraintSpec]

    @property
    def is_integer(self) -> bool:
        return any(v.cat in ("integer", "binary") for v in self.variables.values())


@dataclass
class SolutionResult:
    status: str
    objective_value: Optional[float]
    variables: dict[str, Optional[float]]
    duals: dict[str, Optional[float]] = field(default_factory=dict)
    slacks: dict[str, float] = field(default_factory=dict)
    reduced_costs: dict[str, Optional[float]] = field(default_factory=dict)
