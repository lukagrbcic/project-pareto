#####################################################################################################
# PARETO was produced under the DOE Produced Water Application for Beneficial Reuse Environmental
# Impact and Treatment Optimization (PARETO), and is copyright (c) 2021-2026 by the software owners:
# The Regents of the University of California, through Lawrence Berkeley National Laboratory, et al.
# All rights reserved.
#
# NOTICE. This Software was developed under funding from the U.S. Department of Energy and the U.S.
# Government consequently retains certain rights. As such, the U.S. Government has been granted for
# itself and others acting on its behalf a paid-up, nonexclusive, irrevocable, worldwide license in
# the Software to reproduce, distribute copies to the public, prepare derivative works, and perform
# publicly and display publicly, and to permit others to do so.
#####################################################################################################
"""Stress-test the packaged strategic toy case.

Examples:

    python stress_test_toy.py
    python stress_test_toy.py --suite config
    python stress_test_toy.py --suite solver
    python stress_test_toy.py --suite missing
    python stress_test_toy.py --suite missing --missing-mode entry --solve-missing
    python stress_test_toy.py --suite derived
    python stress_test_toy.py --suite all

The default run executes only the known baseline. Missing-data experiments build
models but do not solve them unless ``--solve-missing`` is supplied. Every trial
starts from a deep copy of the workbook data, so this script never edits the
packaged Excel case study.
"""

import argparse
import csv
from collections.abc import Mapping, MutableMapping
from contextlib import redirect_stdout
from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
import hashlib
from importlib import resources
import io
import json
import math
from pathlib import Path
from time import perf_counter
from typing import Any
import warnings

from idaes.core.util.model_statistics import degrees_of_freedom
import pyomo.environ as pyo

from pareto.strategic_water_management.strategic_produced_water_optimization import (
    create_model,
    solve_model,
)
from pareto.utilities.enums import (
    DesalinationModel,
    Hydraulics,
    InfrastructureTiming,
    Objectives,
    PipelineCapacity,
    PipelineCost,
    RemovalEfficiencyMethod,
    SubsurfaceRisk,
    WaterQuality,
)
from pareto.utilities.get_data import get_data
from pareto.utilities.results import is_feasible

CASE_STUDY = "strategic_toy_case_study.xlsx"
BASELINE_OBJECTIVE = 6122.5178
DEFAULT_RESULTS_PATH = Path("stress_test_toy_results.csv")
DEFAULT_DERIVED_PATH = Path("stress_test_toy_derived_parameters.csv")

BASE_CONFIG = {
    "objective": Objectives.cost,
    "pipeline_cost": PipelineCost.distance_based,
    "pipeline_capacity": PipelineCapacity.input,
    "hydraulics": Hydraulics.false,
    "desalination_model": DesalinationModel.false,
    "node_capacity": True,
    "water_quality": WaterQuality.false,
    "removal_efficiency_method": RemovalEfficiencyMethod.concentration_based,
    "infrastructure_timing": InfrastructureTiming.true,
    "subsurface_risk": SubsurfaceRisk.false,
}

BASE_OPTIONS = {
    "deactivate_slacks": True,
    "scale_model": False,
    "scaling_factor": 1000,
    "running_time": 200,
    "gap": 0,
}

RESULT_FIELDS = [
    "experiment",
    "category",
    "mutation",
    "config",
    "options",
    "build_status",
    "solve_status",
    "solver_status",
    "termination_condition",
    "feasible",
    "objective_name",
    "objective_value",
    "baseline_objective_delta",
    "build_seconds",
    "solve_seconds",
    "build_degrees_of_freedom",
    "solved_degrees_of_freedom",
    "variables",
    "binary_variables",
    "active_constraints",
    "total_sourced",
    "total_trucked",
    "total_disposed",
    "total_reused",
    "total_beneficial_reuse",
    "max_slack",
    "selected_infrastructure",
    "warnings",
    "error_phase",
    "error_type",
    "error_message",
]

DERIVED_FIELDS = [
    "record_type",
    "name",
    "index",
    "status",
    "raw_value",
    "effective_value",
    "units",
    "mutable",
]


@dataclass(frozen=True)
class Experiment:
    name: str
    category: str
    config_overrides: dict[str, Any] = field(default_factory=dict)
    options_overrides: dict[str, Any] = field(default_factory=dict)
    missing_parameter: str | None = None
    missing_mode: str | None = None
    solve: bool = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        action="append",
        choices=("baseline", "config", "solver", "missing", "derived", "all"),
        help="Experiment suite to run; may be supplied more than once (default: baseline).",
    )
    parser.add_argument(
        "--solver",
        default="cbc",
        help="Pyomo solver name used for solved experiments (default: cbc).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=200,
        help="Baseline solver time limit in seconds (default: 200).",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Build experiments and collect model-size metrics without solving.",
    )
    parser.add_argument(
        "--solve-missing",
        action="store_true",
        help="Solve missing-data experiments that build successfully.",
    )
    parser.add_argument(
        "--missing-mode",
        action="append",
        choices=("sheet", "entry", "nan"),
        help="Missing-data mutation; may be repeated (default: sheet).",
    )
    parser.add_argument(
        "--missing-parameter",
        action="append",
        help="Limit missing-data experiments to this parameter; may be repeated.",
    )
    parser.add_argument(
        "--max-missing",
        type=int,
        help="Limit the number of parameter names included in the missing-data suite.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_RESULTS_PATH,
        help=f"Experiment CSV path (default: {DEFAULT_RESULTS_PATH}).",
    )
    parser.add_argument(
        "--derived-output",
        type=Path,
        default=DEFAULT_DERIVED_PATH,
        help=f"Derived-parameter CSV path (default: {DEFAULT_DERIVED_PATH}).",
    )
    parser.add_argument(
        "--fail-on-error",
        action="store_true",
        help="Return a nonzero exit code if any non-baseline experiment fails.",
    )
    return parser.parse_args()


def load_case() -> tuple[dict[str, Any], dict[str, Any]]:
    case_resource = resources.files("pareto.case_studies").joinpath(CASE_STUDY)
    with resources.as_file(case_resource) as case_path:
        df_sets, df_parameters = get_data(case_path, model_type="strategic")
    return df_sets, df_parameters


def config_experiments() -> list[Experiment]:
    return [
        Experiment(
            "config_pipeline_cost_capacity",
            "config",
            {"pipeline_cost": PipelineCost.capacity_based},
        ),
        Experiment(
            "config_pipeline_capacity_calculated",
            "config",
            {"pipeline_capacity": PipelineCapacity.calculated},
        ),
        Experiment("config_node_capacity_off", "config", {"node_capacity": False}),
        Experiment(
            "config_water_quality_post_process",
            "config",
            {"water_quality": WaterQuality.post_process},
        ),
        Experiment(
            "config_water_quality_discrete",
            "config",
            {"water_quality": WaterQuality.discrete},
        ),
        Experiment(
            "config_hydraulics_post_process",
            "config",
            {"hydraulics": Hydraulics.post_process},
        ),
        Experiment(
            "config_hydraulics_linearized",
            "config",
            {"hydraulics": Hydraulics.co_optimize_linearized},
        ),
        Experiment(
            "config_infrastructure_timing_off",
            "config",
            {"infrastructure_timing": InfrastructureTiming.false},
        ),
        Experiment(
            "config_subsurface_risk_metrics",
            "config",
            {"subsurface_risk": SubsurfaceRisk.calculate_risk_metrics},
        ),
        Experiment(
            "config_removal_efficiency_load",
            "config",
            {"removal_efficiency_method": RemovalEfficiencyMethod.load_based},
        ),
        Experiment(
            "config_objective_reuse",
            "config",
            {"objective": Objectives.reuse},
        ),
        Experiment(
            "config_objective_environmental",
            "config",
            {"objective": Objectives.environmental},
        ),
        Experiment(
            "config_objective_subsurface_risk",
            "config",
            {"objective": Objectives.subsurface_risk},
        ),
    ]


def solver_experiments() -> list[Experiment]:
    return [
        Experiment(
            "solver_timeout_10", "solver", options_overrides={"running_time": 10}
        ),
        Experiment(
            "solver_timeout_60", "solver", options_overrides={"running_time": 60}
        ),
        Experiment("solver_gap_0_001", "solver", options_overrides={"gap": 0.001}),
        Experiment("solver_gap_0_01", "solver", options_overrides={"gap": 0.01}),
        Experiment("solver_gap_0_05", "solver", options_overrides={"gap": 0.05}),
        Experiment(
            "solver_scaling_1000",
            "solver",
            options_overrides={"scale_model": True, "scaling_factor": 1000},
        ),
        Experiment(
            "solver_scaling_1000000",
            "solver",
            options_overrides={"scale_model": True, "scaling_factor": 1000000},
        ),
        Experiment(
            "solver_slacks_enabled",
            "solver",
            options_overrides={"deactivate_slacks": False},
        ),
    ]


def missing_experiments(
    parameter_names: list[str], modes: list[str], solve: bool
) -> list[Experiment]:
    experiments = []
    for parameter_name in parameter_names:
        for mode in modes:
            experiments.append(
                Experiment(
                    name=f"missing_{mode}_{parameter_name}",
                    category="missing",
                    missing_parameter=parameter_name,
                    missing_mode=mode,
                    solve=solve,
                )
            )
    return experiments


def select_experiments(
    args: argparse.Namespace, parameter_names: list[str]
) -> list[Experiment]:
    suites = args.suite or ["baseline"]
    if "all" in suites:
        suites = ["baseline", "config", "solver", "missing", "derived"]

    experiments = []
    if suites:
        solve_baseline = any(
            suite in suites for suite in ("baseline", "config", "solver")
        )
        experiments.append(Experiment("baseline", "baseline", solve=solve_baseline))
    if "config" in suites:
        experiments.extend(config_experiments())
    if "solver" in suites:
        experiments.extend(solver_experiments())
    if "missing" in suites:
        modes = args.missing_mode or ["sheet"]
        experiments.extend(
            missing_experiments(parameter_names, modes, solve=args.solve_missing)
        )

    unique = {}
    for experiment in experiments:
        unique[experiment.name] = experiment
    return list(unique.values())


def apply_missing_mutation(parameters: dict[str, Any], experiment: Experiment) -> str:
    name = experiment.missing_parameter
    mode = experiment.missing_mode
    if not name or not mode:
        return ""
    if name not in parameters:
        raise KeyError(f"Parameter {name!r} is not present in the loaded workbook data")

    if mode == "sheet":
        parameters.pop(name)
        return f"removed parameter {name}"

    values = parameters[name]
    if not isinstance(values, MutableMapping) or not values:
        raise TypeError(f"Parameter {name!r} has no mutable indexed entries")
    first_index = next(iter(values))
    if mode == "entry":
        values.pop(first_index)
        return f"removed {name}[{first_index!r}]"
    if mode == "nan":
        values[first_index] = math.nan
        return f"set {name}[{first_index!r}] to NaN"
    raise ValueError(f"Unsupported missing-data mode: {mode}")


def json_ready(value: Any) -> Any:
    if isinstance(value, Enum):
        return f"{type(value).__name__}.{value.name}"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass
    return value


def as_json(value: Any) -> str:
    return json.dumps(json_ready(value), sort_keys=True, default=str)


def component_value(model: pyo.ConcreteModel, name: str) -> float | None:
    component = model.find_component(name)
    if component is None:
        return None
    value = pyo.value(component, exception=False)
    return float(value) if value is not None else None


def max_slack(model: pyo.ConcreteModel) -> float | None:
    values = []
    for component in model.component_objects(pyo.Var, descend_into=True):
        if not component.local_name.startswith("v_S_"):
            continue
        for variable in component.values():
            if variable.value is not None:
                values.append(abs(float(variable.value)))
    return max(values) if values else None


def selected_infrastructure(model: pyo.ConcreteModel) -> str:
    selected = {}
    for name in (
        "vb_y_Pipeline",
        "vb_y_Storage",
        "vb_y_Treatment",
        "vb_y_Disposal",
        "vb_y_BeneficialReuse",
    ):
        component = model.find_component(name)
        if component is None:
            continue
        indexes = [
            repr(index)
            for index, variable in component.items()
            if variable.value is not None and variable.value > 0.5
        ]
        if indexes:
            selected[name] = {
                "count": len(indexes),
                "indexes": indexes[:20],
                "signature": hashlib.sha256("|".join(indexes).encode()).hexdigest()[
                    :16
                ],
                "truncated": len(indexes) > 20,
            }
    return as_json(selected)


def collect_model_metrics(model: pyo.ConcreteModel, row: dict[str, Any]) -> None:
    variables = list(model.component_data_objects(pyo.Var, descend_into=True))
    constraints = list(
        model.component_data_objects(pyo.Constraint, active=True, descend_into=True)
    )
    row["variables"] = len(variables)
    row["binary_variables"] = sum(variable.is_binary() for variable in variables)
    row["active_constraints"] = len(constraints)


def collect_solution_metrics(model: pyo.ConcreteModel, row: dict[str, Any]) -> None:
    objectives = list(
        model.component_data_objects(pyo.Objective, active=True, descend_into=True)
    )
    objective_values = [
        (objective.name, pyo.value(objective, exception=False))
        for objective in objectives
    ]
    if objective_values:
        row["objective_name"] = ";".join(name for name, _ in objective_values)
        row["objective_value"] = objective_values[0][1]

    row["total_sourced"] = component_value(model, "v_F_TotalSourced")
    row["total_trucked"] = component_value(model, "v_F_TotalTrucked")
    row["total_disposed"] = component_value(model, "v_F_TotalDisposed")
    row["total_reused"] = component_value(model, "v_F_TotalReused")
    row["total_beneficial_reuse"] = component_value(model, "v_F_TotalBeneficialReuse")
    row["max_slack"] = max_slack(model)
    row["selected_infrastructure"] = selected_infrastructure(model)


def empty_result(experiment: Experiment, config: dict, options: dict) -> dict[str, Any]:
    row = {field: "" for field in RESULT_FIELDS}
    row.update(
        {
            "experiment": experiment.name,
            "category": experiment.category,
            "config": as_json(config),
            "options": as_json(options),
            "build_status": "not_run",
            "solve_status": "not_run",
        }
    )
    return row


def append_warnings(row: dict[str, Any], caught: list[warnings.WarningMessage]) -> None:
    messages = [f"{warning.category.__name__}: {warning.message}" for warning in caught]
    if not messages:
        return
    existing = row["warnings"]
    row["warnings"] = " | ".join(filter(None, [existing, *messages]))


def record_error(row: dict[str, Any], phase: str, error: Exception) -> None:
    row["error_phase"] = phase
    row["error_type"] = type(error).__name__
    row["error_message"] = str(error)


def run_experiment(
    experiment: Experiment,
    base_sets: dict[str, Any],
    base_parameters: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[dict[str, Any], pyo.ConcreteModel | None]:
    config = {**BASE_CONFIG, **experiment.config_overrides}
    options = {
        **BASE_OPTIONS,
        "solver": args.solver,
        "running_time": args.timeout,
        **experiment.options_overrides,
    }
    row = empty_result(experiment, config, options)
    trial_sets = deepcopy(base_sets)
    trial_parameters = deepcopy(base_parameters)

    try:
        row["mutation"] = apply_missing_mutation(trial_parameters, experiment)
    except Exception as error:
        row["build_status"] = "error"
        record_error(row, "mutation", error)
        return row, None

    build_start = perf_counter()
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            model = create_model(trial_sets, trial_parameters, default=config)
        append_warnings(row, caught)
        row["build_status"] = "ok"
        row["build_seconds"] = perf_counter() - build_start
        row["build_degrees_of_freedom"] = degrees_of_freedom(model)
        collect_model_metrics(model, row)
    except Exception as error:
        row["build_status"] = "error"
        row["build_seconds"] = perf_counter() - build_start
        record_error(row, "build", error)
        return row, None

    should_solve = experiment.solve and not args.build_only
    if not should_solve:
        row["solve_status"] = "skipped"
        return row, model

    solve_start = perf_counter()
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            results = solve_model(model=model, options=options)
        append_warnings(row, caught)
        row["solve_seconds"] = perf_counter() - solve_start
        row["solver_status"] = str(results.solver.status)
        row["termination_condition"] = str(results.solver.termination_condition)
        row["solve_status"] = "ok"
        row["solved_degrees_of_freedom"] = degrees_of_freedom(model)
        collect_solution_metrics(model, row)
        with redirect_stdout(io.StringIO()):
            row["feasible"] = is_feasible(model)
        if experiment.name == "baseline" and row["objective_value"] != "":
            row["baseline_objective_delta"] = (
                float(row["objective_value"]) - BASELINE_OBJECTIVE
            )
    except Exception as error:
        row["solve_status"] = "error"
        row["solve_seconds"] = perf_counter() - solve_start
        record_error(row, "solve", error)
    return row, model


def flatten_parameters(parameters: dict[str, Any]) -> dict[tuple[str, str], Any]:
    flattened = {}
    for name, values in parameters.items():
        if isinstance(values, Mapping):
            for index, value in values.items():
                flattened[(name, repr(index))] = value
        else:
            flattened[(name, "")] = values
    return flattened


def comparable(value: Any) -> str:
    return as_json(value)


def collect_derived_parameters(
    raw_parameters: dict[str, Any], model: pyo.ConcreteModel
) -> list[dict[str, Any]]:
    rows = []
    raw = flatten_parameters(raw_parameters)
    effective = flatten_parameters(model.df_parameters)
    for name, index in sorted(set(raw) | set(effective)):
        raw_value = raw.get((name, index), "")
        effective_value = effective.get((name, index), "")
        if (name, index) not in raw:
            status = "added_by_preprocessing"
        elif (name, index) not in effective:
            status = "removed_by_preprocessing"
        elif comparable(raw_value) != comparable(effective_value):
            status = "changed_by_preprocessing"
        else:
            continue
        rows.append(
            {
                "record_type": "input_preprocessing",
                "name": name,
                "index": index,
                "status": status,
                "raw_value": comparable(raw_value),
                "effective_value": comparable(effective_value),
                "units": "",
                "mutable": "",
            }
        )

    for component in model.component_objects(pyo.Param, descend_into=True):
        for index, parameter in component.items():
            try:
                units = str(pyo.units.get_units(parameter))
            except (TypeError, ValueError):
                units = ""
            rows.append(
                {
                    "record_type": "pyomo_parameter",
                    "name": component.name,
                    "index": repr(index),
                    "status": "effective_model_value",
                    "raw_value": "",
                    "effective_value": comparable(
                        pyo.value(parameter, exception=False)
                    ),
                    "units": units,
                    "mutable": component.mutable,
                }
            )
    return rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def print_result(row: dict[str, Any]) -> None:
    summary = [
        f"build={row['build_status']}",
        f"solve={row['solve_status']}",
    ]
    if row["termination_condition"] != "":
        summary.append(f"termination={row['termination_condition']}")
    if row["objective_value"] != "":
        summary.append(f"objective={float(row['objective_value']):.6g}")
    if row["error_type"]:
        summary.append(
            f"error={row['error_phase']}:{row['error_type']}: {row['error_message']}"
        )
    print(f"[{row['experiment']}] " + ", ".join(summary))


def main() -> int:
    args = parse_args()
    base_sets, base_parameters = load_case()

    parameter_names = sorted(
        name for name in base_parameters if name != "proprietary_data"
    )
    if args.missing_parameter:
        requested = set(args.missing_parameter)
        unknown = requested - set(parameter_names)
        if unknown:
            raise SystemExit("Unknown parameter name(s): " + ", ".join(sorted(unknown)))
        parameter_names = [name for name in parameter_names if name in requested]
    if args.max_missing is not None:
        if args.max_missing < 1:
            raise SystemExit("--max-missing must be at least 1")
        parameter_names = parameter_names[: args.max_missing]

    suites = args.suite or ["baseline"]
    if "all" in suites:
        suites = ["baseline", "config", "solver", "missing", "derived"]
    experiments = select_experiments(args, parameter_names)
    print(f"Loaded {CASE_STUDY}; running {len(experiments)} experiment(s).")

    rows = []
    baseline_model = None
    for experiment in experiments:
        row, model = run_experiment(experiment, base_sets, base_parameters, args)
        rows.append(row)
        write_csv(args.output, RESULT_FIELDS, rows)
        print_result(row)
        if experiment.name == "baseline":
            baseline_model = model

    if "derived" in suites:
        if baseline_model is None:
            raise SystemExit(
                "The baseline model could not be built; no derived report written"
            )
        derived_rows = collect_derived_parameters(base_parameters, baseline_model)
        write_csv(args.derived_output, DERIVED_FIELDS, derived_rows)
        print(
            f"Wrote {len(derived_rows)} derived/input parameter records to "
            f"{args.derived_output}"
        )

    print(f"Wrote {len(rows)} experiment result(s) to {args.output}")
    failures = sum(
        row["build_status"] == "error" or row["solve_status"] == "error" for row in rows
    )
    baseline_failed = any(
        row["category"] == "baseline"
        and (row["build_status"] == "error" or row["solve_status"] == "error")
        for row in rows
    )
    return 1 if baseline_failed or (args.fail_on_error and failures) else 0


if __name__ == "__main__":
    raise SystemExit(main())
