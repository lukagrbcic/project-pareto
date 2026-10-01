# PARETO Input Validation Gap Report

## Executive Summary

PARETO contains useful input checks, but it does not currently provide a
single, configuration-aware preflight validator for Excel workbooks.

The strategic model automatically checks some required sheets,
configuration-dependent sheets, optional component dependencies, and coarse
aggregate feasibility. The operational model does not invoke the same
required-data or aggregate-feasibility checks. Both models still rely heavily
on Pyomo parameter defaults and construction-time exceptions.

The main correctness risk is that a workbook can be accepted and solved while
individual missing entries silently receive defaults. For example:

- A missing `PadRates[(P01, T03)]` entry becomes zero production.
- A missing pipeline capacity entry commonly becomes zero capacity.
- A missing elevation entry becomes 100 meters when hydraulics is active.
- A missing cost can receive a code-defined fallback rather than producing an
  error.

These behaviors may be intentional for sparse inputs, but the current system
does not report which values were explicit, which were defaulted, and why.
Consequently, a mathematically optimal solution may describe a different
system from the one the user intended.

The highest-priority improvement is a shared schema-driven validation layer,
invoked by both strategic and operational model builders before unit
conversion and Pyomo construction. It should report all errors, warnings,
defaults, and inactive inputs with workbook sheet and index context.

## Scope

This report covers validation of Excel inputs used by:

- `pareto/strategic_water_management/strategic_produced_water_optimization.py`
- `pareto/operational_water_management/operational_produced_water_optimization_model.py`
- `pareto/utilities/get_data.py`
- `pareto/utilities/process_data.py`
- `pareto/utilities/units_support.py`
- `pareto/utilities/results.py`

It distinguishes four concepts:

1. **Parsing:** Can the workbook and sheet layout be read?
2. **Schema validation:** Are required sheets, columns, indexes, types, and
   values present and valid?
3. **Model validation:** Are the configured network and forecasts internally
   consistent and plausibly feasible?
4. **Real-world completeness:** Does the workbook contain every actual asset,
   connection, and planning alternative?

The fourth concept cannot be established from the workbook alone. PARETO uses
a closed-world assumption: an omitted facility or arc is interpreted as not
existing or not being allowed. Detecting real-world omissions requires an
external source of truth such as GIS data, an asset registry, or an explicit
system manifest.

## Current Validation Flow

The typical strategic flow is:

```text
Excel workbook
    -> get_data()
    -> check_required_data()
    -> units_setup()
    -> Pyomo model construction
    -> model_infeasibility_detection()
    -> solve
    -> optional caller-managed is_feasible()
```

The typical operational flow is:

```text
Excel workbook
    -> get_data()
    -> units_setup()
    -> Pyomo model construction
    -> solve
    -> optional caller-managed is_feasible()
```

The operational model does not currently call `check_required_data()` or
`model_infeasibility_detection()`.

## Existing Validation Coverage

| Capability | Strategic | Operational | Automatically invoked |
|---|---:|---:|---:|
| Recognized sheet-name registry | Yes | Yes | By `get_data()` |
| Unexpected sheet warning | Yes | Yes | By `get_data()` |
| Sheet parsing diagnostics | Yes | Yes | By `get_data()` |
| Strict parsing with `raises=True` | Available | Available | No; default is `False` |
| Required sheet checks | Yes | No | Strategic only |
| Configuration-dependent sheet checks | Yes | No | Strategic only |
| Optional component dependency warnings | Yes | No | Strategic only |
| Missing sheet default insertion | Yes | No equivalent | Strategic only |
| Set consistency helper | Available | Available | No |
| Unit setup and conversion | Yes | Yes | During model build |
| Aggregate supply/capacity screening | Yes | No | Strategic only |
| Aggregate demand/availability screening | Yes | No | Strategic only |
| Pyomo index/domain checks | Partial | Partial | During model build |
| Post-solve feasibility check | Available | Available | Caller-managed |
| Effective default/index report | Standalone toy script only | No | No |

### Workbook Parsing

`pareto/utilities/get_data.py` provides:

- Recognized sheet lists at lines 37-210.
- `DataLoadingError` at lines 255-273.
- Sheet parsing in `_sheets_to_dfs()` at lines 276-303.
- Workbook preprocessing in `_read_data()` at lines 306-494.
- Public loading through `get_data()` at lines 544-648.

Current strengths:

- Unknown sheet names are reported.
- Multiple sheet parsing errors can be summarized together.
- Set and parameter sheets are normalized into dictionaries suitable for
  Pyomo.
- `raises=True` can convert sheet parsing failures into an exception.

Current limitations:

- Strict parsing is disabled by default.
- A failed sheet can be replaced by an empty dataframe and processing can
  continue.
- Sheet existence is often checked by dictionary key, so an empty or
  header-only required sheet can appear to be present.
- Sheet layout is inferred heuristically instead of being validated against a
  declared schema.
- Duplicate parameter indexes can be overwritten during dictionary
  conversion.
- Duplicate set members are not diagnosed.
- No workbook template/version identifier is checked.
- No required-column, index-arity, or header-type validation exists.
- Misspelled sheet names do not receive close-match suggestions.

### Strategic Required Data

`check_required_data()` is defined in
`pareto/utilities/process_data.py:84-690` and is called by strategic
`create_model()` at
`pareto/strategic_water_management/strategic_produced_water_optimization.py:859-862`.

It currently checks:

- The `Units` sheet.
- At least one source-category set.
- At least one sink-category set.
- Hydraulics sheets when hydraulics is active.
- Desalination surrogate data when desalination is active.
- Pipeline cost and pipeline capacity inputs for the chosen formulations.
- Node capacities when node-capacity constraints are active.
- Subsurface risk data when risk calculation is active.
- Objective-specific data for risk, environmental, and surrogate objectives.
- Water-quality component data when water quality is active.
- Infrastructure lead-time sheets when infrastructure timing is active.
- Dependent sheets and arc categories for optional node types.

Important limitations:

- Checks are primarily sheet-key checks, not content checks.
- A required but empty sheet can pass the sheet-presence test.
- Individual missing rows or indexes are not generally detected.
- Missing optional data is commonly replaced with an empty dictionary and a
  warning.
- The validator mutates the input dictionaries by inserting defaults.
- Supplied but inactive feature data is not reported.
- Configuration compatibility is not validated beyond required sheet lists.

### Optional Component Checks

`_check_optional_data()` is defined in
`pareto/utilities/process_data.py:928-984`.

It checks whether an optional set, such as `ProductionPads`, `TreatmentSites`,
or `StorageSites`, has dependent sheets and at least one relevant arc category.

Limitations:

- A set is considered present based on its key, not whether it has members.
- An arc category is considered present based on its sheet key, not whether it
  contains usable arcs.
- At least one arc sheet is required for a node type, but per-node
  connectivity is not checked.
- Missing dependencies are frequently defaulted rather than rejected.

### Set Consistency Helper

`set_consistency_check()` exists at
`pareto/utilities/get_data.py:651-695`.

It can identify parameter index tokens that are not present in supplied sets.
It is not automatically called by either model builder.

It also checks membership against the union of all provided sets rather than
checking each tuple position. Therefore, for `(origin, destination, time)`, it
does not prove that the origin belongs to the origin set, the destination
belongs to the destination set, and the time belongs to the time set.

Most importantly, it detects unexpected indexes but does not detect missing
expected indexes.

### Aggregate Feasibility Screening

`model_infeasibility_detection()` is defined in
`pareto/utilities/process_data.py:693-907` and is called by strategic
`create_model()` at lines 3132-3133.

It checks:

- Produced-water volume against aggregate maximum sink capacity by period.
- Completion demand against aggregate produced water, external water, and
  storage carryover.

Limitations:

- It is strategic-only.
- It is aggregate rather than topology-aware.
- It does not verify that water can reach a sink through the declared arcs.
- It does not account for network cuts or detailed arc capacities.
- It does not fully represent infrastructure lead-time availability.
- Time periods are processed using Python `sorted()`, which can misorder labels
  such as `T1`, `T2`, and `T10`.
- Demand checking raises inside the period loop, so later deficient periods
  may not be included in the same diagnostic.
- No explicit tolerance or non-finite-value guard is applied.

The function comments state that source/sink node connectivity is checked, but
the implementation shown at lines 693-907 performs aggregate capacity and
demand checks only.

### Post-Solve Feasibility

`is_feasible()` is defined in `pareto/utilities/results.py:2923-2972`.

It checks variable bounds, integer/binary domains, and active constraint
residuals. It is called manually by the example runners, not automatically by
the solver wrappers or report generator.

There is also a correctness gap: a continuous-variable bound violation is
printed at lines 2936-2945, but that path does not immediately return `False`.
Binary/integer and constraint violations do return `False`.

## Missing Validation Capabilities

### 1. Declarative Workbook Schema

There is no central schema declaring, for every sheet:

- Applicable model types.
- Activation condition.
- Required or optional status.
- Expected layout and header row.
- Index columns and index arity.
- Positional set membership.
- Value type.
- Unit dimension.
- Valid range/domain.
- Sparse versus complete coverage policy.
- Default policy and default provenance.

Without this schema, validation rules and defaults remain distributed across
the loader, model builders, and Pyomo declarations.

### 2. Required Sheet Content

The current code does not systematically distinguish:

- An absent sheet.
- An empty sheet.
- A header-only sheet.
- An unparseable sheet replaced by an empty dataframe.
- A sheet containing only invalid or blank values.
- A valid but inactive sheet.

A required sheet should be required to contain its expected scalar rows or at
least one valid indexed row.

### 3. Positional Index Validation

There is no comprehensive foreign-key validation for indexed parameters.

For example, validation should establish:

```text
PadRates[(P01, T03)]
    P01 belongs to ProductionPads
    T03 belongs to TimePeriods
```

Current Pyomo construction catches some unexpected initializer keys, but the
resulting messages are not consistently translated into workbook sheet/index
diagnostics.

### 4. Expected Index Coverage

There is no general check that every required index has a value.

Examples:

- Every production pad and period has `PadRates`.
- Every completion pad and period has `CompletionsDemand` and
  `FlowbackRates`.
- Every active pipeline arc has a capacity and applicable cost.
- Every disposal site has capacity and operating cost.
- Every active network location has elevation when hydraulics is enabled.
- Every treatment site/technology/component combination has the required
  efficiency data.

This is the largest current correctness gap because Pyomo defaults often mask
missing indexes.

The validator must support both complete and intentionally sparse parameters.
A full Cartesian product is not correct for every sheet, especially arc-based
inputs.

### 5. Time-Series Alignment

`TimePeriods` is derived from `CompletionsDemand` columns in
`get_data.py:637-643`.

Missing checks include:

- Unique and nonblank time labels.
- Natural or chronological ordering.
- A canonical time set independent of one parameter sheet.
- Complete per-entity coverage across all required time-indexed sheets.
- Duplicate periods.
- Gaps in time series.
- Consistent horizon start/end.
- Decision-period duration consistency.
- Mismatched periods present in another sheet but absent from
  `CompletionsDemand`.

### 6. Blank, NaN, and Non-Finite Values

There is no uniform policy that distinguishes:

- Explicit zero.
- Blank input.
- Missing index.
- Defaulted zero.
- `NaN`.
- Positive or negative infinity.
- Spreadsheet formula errors or formulas returning blank values.

Numeric inputs should be checked with finite-number validation before unit
conversion and Pyomo construction. Diagnostics should identify the workbook,
sheet, row/column or parameter index, and raw value.

### 7. Value Types and Domains

Most workbook parameter values do not receive explicit domain validation
before model construction.

Missing checks include:

- Capacities, rates, distances, diameters, times, and most costs are finite and
  nonnegative.
- Efficiencies and operating fractions lie in `[0, 1]`.
- Binary flags are exactly `0` or `1`.
- Lead times and period-count durations are valid nonnegative values and,
  where appropriate, integral.
- Pressures, roughness values, and hydraulic coefficients are physically
  valid.
- Concentrations and salinities are finite and nonnegative.
- Enumerated codes use supported values.
- Scalar economics rows exist and have numeric values.

### 8. Relational Validation

Individual values may be valid while their relationship is invalid. Missing
checks include:

- Initial storage level does not exceed storage capacity.
- Reuse minimum does not exceed reuse capacity.
- Minimum truck flow does not exceed maximum truck flow.
- Initial pipeline diameters belong to allowed diameter options.
- Capacity increments have compatible expansion costs.
- Treatment sites, technologies, capacities, and removal data align.
- Infrastructure lead time is meaningful within the planning horizon.
- Minimum pressure does not exceed maximum pressure.
- Midstream MVC minimum does not exceed MVC maximum.
- Midstream tariff, penalty, credit caps, duration, and make-up factors use
  valid ranges.

### 9. Topology and Connectivity

There is no complete graph-level preflight check for:

- Invalid endpoint-type combinations.
- Duplicate arcs.
- Self-loops.
- Reverse-duplicate policy.
- Arc rows whose value is zero but whose key still enters topology.
- Isolated facilities.
- Sources with no route to any valid sink.
- Completion demand unreachable from any supply source.
- Transshipment nodes without both usable inflow and outflow.
- Disconnected network components.
- Capacity-aware cuts that make demand unreachable.
- Differences between piping and trucking reachability.
- Compatibility between topology and active treatment stream definitions.

Aggregate supply and capacity checks cannot detect these conditions.

### 10. Active and Inactive Configuration Data

Missing diagnostics include:

- Sheets supplied but ignored because their feature is disabled.
- A feature partially configured but silently inactive.
- Incompatible configuration combinations.
- Complete per-index coverage for active optional features.
- An explicit policy for whether inactive data is ignored, validated, or
  rejected.

For example, water-quality sheets may be present while
`water_quality=WaterQuality.false`. A preflight report should state that those
inputs are inactive rather than allowing users to assume they affected the
solution.

### 11. Default Provenance

Defaults are distributed across multiple layers:

- `check_required_data()` inserts sheet-level defaults.
- `build_common_params()` defines common Pyomo defaults.
- Strategic and operational builders define model-specific defaults.
- Some values are derived during preprocessing.

There is no effective-input manifest with fields such as:

```text
parameter
index
raw workbook value
effective model value
default source
default reason
derived/default/explicit status
```

Representative code-defined defaults include:

| Parameter type | Current fallback |
|---|---:|
| Many demands, rates, capacities, and arc indicators | `0` |
| Offloading/processing capacities | Large code-defined capacity |
| Truck capacity | `110 bbl` |
| Trucking time | `12 hours` |
| Elevation | `100 m` |
| Treatment efficiency | `1.0` |
| Several Big-M and slack penalties | `99999` |
| Economics discount rate | `0.08` |
| Economics CAPEX lifetime | `20` |

These may be intentional, but users cannot currently audit where they were
applied.

### 12. Units Validation and Safe Parsing

`units_setup()` is defined in `pareto/utilities/units_support.py:38-190`.

Missing checks include:

- Presence of every required unit key.
- Supported aliases and clear invalid-unit diagnostics.
- Expected dimensionality for each sheet.
- Rejection of mixed units where sheet-global units are assumed.
- Validation after conversion.

The current implementation dynamically executes strings derived from workbook
unit values using `exec()` at lines 58-81. This should be replaced by a safe,
whitelisted unit lookup before accepting untrusted workbooks.

### 13. Strategic and Operational Validation Parity

The operational model does not call `check_required_data()` or the strategic
aggregate feasibility checker. Missing operational data typically produces a
raw `KeyError`, unit conversion error, or Pyomo construction error.

Operational-specific gaps include:

- No systematic `Units` requirement check.
- No source/sink requirement check.
- No production-tank dependency validation.
- No operational configuration-dependent sheet validation.
- No operational aggregate feasibility checks.
- No malformed operational workbook tests.

`MinTruckFlow` and `MaxTruckFlow` are accessed by the operational model but are
not standard workbook parameter tabs. The runner injects them directly at
`pareto/operational_water_management/run_operational_model.py:93-95`.
There is no standard validation that both are supplied, finite, nonnegative,
unit-consistent, and ordered correctly.

The operational runner also calls `get_data()` without
`model_type="operational"` at line 91. Custom sheet lists recover the requested
tabs, but the model-type behavior and recognized-sheet warnings remain based on
the default strategic mode.

### 14. User-Facing Diagnostics

Current errors and warnings are fragmented across pandas, Pyomo, unit
conversion, custom exceptions, and ordinary `KeyError` paths.

A structured diagnostic should include:

```text
severity
diagnostic code
model type
configuration
workbook
sheet
cell or parameter index
raw value
effective value
message
suggested correction
default source
```

Validation should collect all independent issues where possible rather than
failing on the first one.

### 15. Reporting and Solve-State Validation

Missing safeguards include:

- Requiring a feasible solution before generating a trusted report.
- Requiring or clearly reporting optimal/feasible solver termination.
- Automatically invoking `is_feasible()` after a solve.
- Distinguishing explicit zero outputs from omitted/default zero inputs.
- Including validation status and effective defaults in result workbooks.

## Concrete Failure Examples

### Missing Time-Series Entry

```text
ERROR IV-TIME-003
Sheet: PadRates
Index: (P01, T03)
Expected coverage: ProductionPads x TimePeriods
Problem: No value was supplied. Current behavior uses Pyomo default 0.
Fix: Supply a finite nonnegative rate or explicitly mark this index as an
intentional zero.
```

### Missing Elevation Entry

```text
WARNING IV-DEFAULT-011
Sheet: Elevation
Index: N03
Feature: Hydraulics.post_process
Problem: The sheet exists, but N03 has no elevation.
Effective value: 100 m
Default source: pareto/utilities/build_utils.py:538-552
```

### Empty Required Sheet

```text
ERROR IV-SHEET-004
Sheet: Units
Problem: The sheet exists but contains no usable unit rows.
Current behavior: Key-presence checks may accept the sheet and fail later in
units_setup().
```

### Invalid Fraction

```text
ERROR IV-RANGE-007
Sheet: TreatmentEfficiency
Index: (R01, MVC)
Value: 1.20
Expected: finite fraction in [0, 1]
```

### Disconnected Production Pad

```text
ERROR IV-TOPOLOGY-006
Node: P04
Type: ProductionPad
Problem: No directed piping or trucking path reaches a disposal, storage,
       treatment, reuse, or completion-demand sink.
Related sheets: PPA, PCA, PNA, PKT, PST, PRT, POT
```

### Inactive Inputs

```text
INFO IV-CONFIG-002
Feature: water quality
Configuration: WaterQuality.false
Problem: Water-quality sheets are present but inactive and do not affect the
optimization result.
```

### Operational Truck Bounds

```text
ERROR IV-RELATION-003
Parameters: MinTruckFlow, MaxTruckFlow
Values: 40000, 37000 bbl/day
Problem: MinTruckFlow must not exceed MaxTruckFlow.
```

### Partial Midstream Configuration

```text
ERROR IV-CONFIG-012
Feature: midstream contracts
Present: MidstreamContracts
Missing or empty: MidstreamReceiptNodes
Problem: The current implementation can silently disable the midstream module.
Fix: Provide both core sets or remove all midstream sheets.
```

## Recommended Design

### Validation API

A shared API could be introduced as:

```python
report = validate_input_data(
    df_sets,
    df_parameters,
    model_type="strategic",
    config=config,
    validation_mode="warn_on_default",
)

report.raise_for_errors()
report.print_summary()
```

Suggested validation modes:

| Mode | Behavior |
|---|---|
| `permissive` | Preserve current defaults and record their use |
| `warn_on_default` | Warn for every sheet/index default |
| `error_on_default` | Reject any undeclared default substitution |

The validator should return typed diagnostic objects rather than only emitting
Python warnings.

### Schema Requirements

Each input definition should provide:

```text
sheet name
model applicability
configuration activation condition
required/optional status
layout
index names and source sets
coverage policy
value type
domain/range
unit dimension
default policy and provenance
relational checks
```

### Validation Order

Recommended execution order:

```text
1. Open workbook and identify template version
2. Parse sheets without substituting silent fallbacks
3. Validate sheet names and layouts
4. Validate sets and duplicate members
5. Build canonical time set
6. Validate parameter index arity and positional membership
7. Validate required index coverage
8. Validate finite values, types, units, and ranges
9. Validate relational constraints
10. Validate active configuration requirements
11. Validate topology and reachability
12. Run aggregate necessary-condition feasibility checks
13. Produce effective-input/default manifest
14. Build the Pyomo model
```

## Prioritized Implementation Plan

### P0: Correctness and Safety

1. Create a shared schema-based validator used by both model builders.
2. Reject non-finite and malformed numeric values before Pyomo construction.
3. Validate positional index membership and required index coverage.
4. Add an operational-specific required-data validator.
5. Add graph-based topology and reachability checks.
6. Replace workbook-driven `exec()` unit parsing with safe lookup.

### P1: Modeling Reliability

1. Introduce an explicit canonical time set and align all time-series inputs.
2. Centralize defaults and generate per-index provenance.
3. Add domain/range and relational validation.
4. Improve aggregate feasibility checks with natural time ordering,
   non-finite guards, and complete issue collection.
5. Report supplied but inactive inputs.
6. Formalize `MinTruckFlow` and `MaxTruckFlow` as supported operational inputs
   or configuration values.

### P2: Diagnostics and Reporting

1. Add diagnostic codes and structured severity levels.
2. Add validation sheets to generated result workbooks.
3. Make report generation visibly conditional on solve and feasibility status.
4. Fix `is_feasible()` to return `False` for continuous bound violations.
5. Add typo suggestions and workbook cell coordinates.

## Required Test Coverage

Add parameterized tests for both strategic and operational models covering:

- Missing required sheet.
- Required sheet present but empty.
- Header-only and malformed sheet.
- Duplicate set members.
- Duplicate parameter indexes.
- Incorrect index tuple arity.
- Unknown set member in each index position.
- Missing expected index.
- Blank, `NaN`, positive infinity, and negative infinity.
- String in a numeric field.
- Incomplete and mismatched time series.
- Duplicate and naturally misordered periods.
- Missing and invalid unit definitions.
- Invalid fractions and binary flags.
- Negative costs, rates, capacities, and distances.
- Invalid min/max and initial-level/capacity relationships.
- Disconnected or isolated network components.
- Unreachable completion demand.
- Inactive feature data.
- Partial midstream configuration.
- Operational truck-flow inputs.
- Effective default provenance.
- Continuous-variable bound violations in `is_feasible()`.
- Report generation for unsolved, infeasible, and non-optimal models.

Existing negative tests in `pareto/tests/test_utilities.py:297-473` cover
missing strategic sheets, warning/default behavior, and two aggregate
infeasibility cases. They do not cover the broader gaps above. The standalone
`stress_test_toy.py` can remove sheets, remove one entry, and inject `NaN`, but
it is an opt-in experiment harness rather than an automatic validation layer.

## Acceptance Criteria

A production-ready input validation layer should satisfy the following:

1. Both strategic and operational models invoke the same validation framework.
2. Every accepted workbook produces a structured validation report.
3. Required sheets must contain valid content, not merely exist.
4. Every parameter has a declared sparse/full coverage policy.
5. Every missing/defaulted index is visible in the report.
6. Every numeric effective value is finite and satisfies its declared domain.
7. All parameter indexes are positionally valid against their source sets.
8. All required time series align to one canonical ordered horizon.
9. Active sources and demands pass topology/reachability checks.
10. Supplied inactive inputs are clearly identified.
11. Default provenance is available by parameter and index.
12. Unit parsing does not execute workbook-provided code.
13. Validation errors identify the sheet and index, preferably the cell.
14. Validation can report multiple independent issues in one run.
15. Result reports include validation, solve status, and feasibility status.

## Conclusion

PARETO's current strategic checks are a useful first layer, especially for
missing configuration-dependent sheets and coarse infeasibility detection.
They are not a comprehensive workbook validator. Key presence often substitutes
for content validity, individual omissions are masked by defaults, and
topology, time-series coverage, ranges, units, and default provenance are not
systematically audited.

Operational validation is the largest parity gap because it does not invoke
the strategic required-data or aggregate feasibility checks. A shared,
schema-driven preflight validator would make model assumptions and fallback
behavior explicit, prevent late and opaque Pyomo errors, and reduce the risk of
optimizing an unintended system.
