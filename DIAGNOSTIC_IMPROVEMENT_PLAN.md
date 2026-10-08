# Diagnostic Improvement Plan — v3 (final)

**The patch is this plan.** No existing PARETO library file is modified. The
deliverable is: two new modules, one new experimental-flagged check layer, and
entry-point wiring in `run_strategic_model.py`. Everything else stays
byte-for-byte.

**Scope:** strategic model only. Operational model parity is out of scope.

---

## 1. Guiding requirement

> Existing validation and optimization diagnostics do not always give users
> enough actionable information to understand why their scenario fails or how
> to fix it.
>
> Improve feedback before and after optimization, expanding validation where
> useful.
>
> Explain common failures in plain language and identify affected facilities,
> connections, tables, or periods. Link findings to the relevant inputs and
> suggest practical next steps. Expand checks for gaps in existing coverage,
> including more complex networks and optional model features. Clearly
> distinguish invalid inputs, infeasibility, inconclusive solver results, and
> execution/reporting errors. Separate confirmed findings from suspected
> causes.

## 2. Final scope — the two wrappers

The work is exactly two additive wrappers around the authors' pipeline:

```text
run_strategic_model.py
  1. get_data(case_study)                          # authors', unchanged
  2. WRAPPER 1 (pre-optimization)                  # NEW
       A. catch-and-explain around check_required_data failures
       B. complementary checks on df_sets/df_parameters   [EXPERIMENTAL]
  3. create_model(case_study)                      # authors', unchanged
       └─ check_required_data()                    # authors', unchanged
  4. solve_model(...)                              # authors', unchanged
  5. WRAPPER 2 (post-optimization)                 # NEW
       - termination classification (read-only)
       - slack scan + opt-in two-phase harvest
       - anchored on is_feasible's verdict
  6. is_feasible(...)                              # authors', unchanged
  7. generate_report(...)                          # authors', unchanged
```

**Untouched (byte-for-byte):** `process_data.py`,
`strategic_produced_water_optimization.py`, `results.py`, `build_utils.py`,
`get_data.py`, and everything under `pareto/operational_water_management`.

**Touched:** only `run_strategic_model.py` (entry-point wiring) plus new files.
Anyone calling `create_model()` directly gets identical behavior to today.

### 2.1 Wrapper 1 — pre-optimization (two distinct parts)

**Part A: catch-and-explain (always on, zero-risk).**
When `check_required_data()` raises (`MissingDataError`,
`DataInfeasibilityError` — process_data.py:915/:921), the entry point catches
it, maps the known message patterns to a plain-language explanation with
affected inputs and suggestions, and **re-raises unchanged**. The authors'
message is preserved verbatim. This part is safe to enable permanently because
it never alters behavior.

```text
What happened:
  The run stopped before the model was built (invalid input).

Confirmed finding:
  Parameter tabs reference the CompletionsPads set, but that set is empty or
  missing — its sheet failed to load and an empty dataframe was used as fallback.

Affected inputs:
  Parameters: CompletionsPadOutsideSystem, CompletionsDemand,
              PadOffloadingCapacity, FlowbackRates

What to do next (pick one):
  1. Recreate the CompletionsPads tab from the template and list the pads.
  2. If those pads are intentionally out of this scenario, remove the 4
     parameter tabs listed above.

Original error (unchanged):
  MissingDataError: Essential data is incomplete. Parameter data for
  CompletionsPads is given, but the "CompletionsPads" Set is missing. ...
```

**Part B: complementary checks (EXPERIMENTAL, warn-only by default).**
`check_required_data()`'s entire scope is **presence + structural relations**
(sheet keys, min-required lists at process_data.py:105-119, param↔set
relations at :928-984, config-dependent sheet requirements at :147-283). It
never checks content, coverage, finiteness, domains, or topology. Part B adds
exactly those net-new checks — **not** based on `check_required_data`, no
shared logic, complementary questions:

| # | Check | Question it answers | Severity / Kind |
|---|---|---|---|
| B1 | Sheet content | is a required sheet absent / empty / header-only / unparseable? | ERROR / CONFIRMED |
| B2 | Positional index membership | does each tuple position come from the *specific* set assigned to it? | ERROR / CONFIRMED |
| B3 | Required-index coverage | is `expected − supplied` empty for complete-coverage params? | ERROR / CONFIRMED |
| B4 | Non-finite rejection | are there NaN/±inf values before unit conversion? | ERROR / CONFIRMED |
| B5 | Domain checks | are fractions in [0,1], volumes ≥ 0, binaries in {0,1}? | ERROR / CONFIRMED |
| B6 | Topology / reachability | is every node reachable from a source (piping-only vs piping+trucking)? | WARNING / SUSPECTED |
| B7 | Sparse-default recording | which entries were explicit vs defaulted? | INFO–WARNING / CONFIRMED |

### 2.2 Wrapper 2 — post-optimization (anchored on `is_feasible`)

`is_feasible()` (results.py:2923-2972) stays untouched and still prints its
documented verdict (docs/utilities/Results.rst:306-337):
- fail → *"Model results are not feasible and should not be trusted"*
- pass → *"Model results validated and found to pass feasibility tests"*

Wrapper 2 adds the **why/where** behind that verdict, as pure reads:

1. **Termination classification** — read
   `results.solver.termination_condition` and classify:
   - `optimal` → normal path.
   - `infeasible` → INFEASIBILITY findings (below).
   - `maxTimeLimit`, `unbounded`, other → SOLVER WARNING: "the solver stopped
     without proving optimality (termination: X); results are not a validated
     solution." (Today only `== infeasible` is tested at
     strategic_produced_water_optimization.py:7159-7224; that behavior is not
     changed, only reported.)
2. **Two-phase slack harvest** — only when the primary solve returns
   `infeasible`, only with user consent, and only as an entry-point
   orchestration step: re-solve once with `deactivate_slacks=False` purely to
   harvest diagnosis. The authoritative result stays infeasible and is never
   replaced. If the harvest also fails, the report says the infeasibility
   could not be localized (SUSPECTED, with the authors' aggregate numbers as
   context).
3. **Slack diagnosis** — scan the nine `v_S_*` slacks
   (build_utils.py:1068-1129) with tolerance 1e-6. Each nonzero slack becomes
   a CONFIRMED finding naming the pad/period/site/arc, the shortfall volume,
   the total `v_C_Slack`, and the `p_psi_*` penalties as provenance.

## 3. Ground truth — the authors' own documentation

The post-solve layer is **not invented semantics**; it operationalizes what the
authors documented:

- `docs/utilities/Results.rst:306-337` — the `is_feasible` contract (verify
  solution, tolerance defaults 1e-3, verdict strings above).
- `docs/model_library/strategic_water_management/index.rst:468` — "additional
  slack variables are included **to facilitate the identification of potential
  issues with input data**".
- `index.rst:1174-1184` — Slack Costs: "In the case that the model is
  infeasible, these slack variables are **used to determine where the
  infeasibility occurs** (e.g. pipeline capacity is not sufficient)."

Documented vs implemented, as the confirmation method:

| Docs say | Code does | Verdict |
|---|---|---|
| `is_feasible` verifies solution, verdicts documented | Matches (results.py:2923-2972) | Documented, working |
| Slacks identify input-data issues / locate infeasibility | Defined, penalized, in objective — **never scanned or reported** | Documented intent, missing implementation → Wrapper 2 |
| (Operational docs) per-constraint slack semantics | Strategic docs lack them | Docs gap, out of scope |
| Termination beyond `infeasible` | Silently treated as valid | Undocumented behavior → Wrapper 2 classification |

## 4. Transcribe, don't invent — anchors for every complementary check

No complementary check is invented from scratch. Each is anchored in
knowledge the authors already embedded:

| Check | Anchor (where the semantics come from) | What's there today |
|---|---|---|
| B1 content | `get_data.py:286-302` (`_sheets_to_dfs` already detects parse failure → empty-df fallback), `:37-210` (recognized-sheet lists, header=1 for param sheets) | Fallback exists, result only a stray warning |
| B2 membership | `get_data.py:651` `set_consistency_check(param, *args)` | Exists as a manual utility; only catches *unexpected* entries, blind to *missing*, not per-position, **never auto-called** (only demonstrated in `tests/toy_case_study.py:204,210`) |
| B3 coverage | `build_utils.py` `Param` signatures, e.g. `p_beta_Production = Param(s_P, s_T, default=0, initialize={df_parameters['PadRates']})` (:197-211) | Every param declares its index sets; nothing ever checks the initializer against them |
| B4 non-finite | `get_data.py:402-409` (`keep_default_na=False`) + `:497-505` (`_cleanup_data` "" → NaN) | The pipeline's own missing-value convention |
| B5 domains | Constraint math + model library docs | Zero `bounds=`/`validate=` in all of build_utils.py (verified by grep) |
| B6 topology | The claim at `process_data.py:694-697` (connectivity checks described, not implemented) + networkx already a dependency (`visualize.py:14`) | Aggregate-only code; stale comment |

Umbrella: `INPUT_VALIDATION_GAP_REPORT.md` (repo root) already specifies this
check catalog — the plan operationalizes it, it doesn't originate it.

What is genuinely new code: the **registry transcription** (typing Param
signatures + sheet lists into dataclasses), the **formatting layer**
(`Diagnostic`/`ValidationReport`), and the **judgment calls** (CONFIRMED vs
SUSPECTED — guarded by the rule: *if a check cannot be anchored in something
the authors declared or documented, it stays SUSPECTED and never raises*).

## 5. Diagnostic data model (`pareto/utilities/diagnostics.py`, new)

```python
@dataclass
class Diagnostic:
    severity: Severity          # ERROR | WARNING | INFO
    kind: Kind                  # CONFIRMED | SUSPECTED
    stage: Stage                # INPUT | TOPOLOGY | INFEASIBILITY | SOLVER | EXECUTION
    code: str                   # e.g. "IV-TIME-003"
    message: str                # plain-language finding
    affected: list              # pads, periods, sites, arcs, sheets affected
    inputs: list                # e.g. ["PadRates[(PP01, T01)]"]
    suggestion: str             # practical next step
    detail: str = ""


class ValidationReport:
    def add(self, diagnostic) -> None: ...
    @property
    def errors(self) -> list: ...
    @property
    def warnings(self) -> list: ...
    def raise_for_errors(self) -> None:
        # imports (never modifies) the authors' exceptions:
        # MissingDataError / DataInfeasibilityError (process_data.py:915/:921)
    def print_summary(self) -> None: ...
```

**Registry (`pareto/utilities/input_schema.py`, new)** — transcription of
`build_utils.py` Param signatures + `get_data.py` sheet lists:

```python
@dataclass(frozen=True)
class SheetSpec:
    index: tuple                     # (("ProductionPads", "s_P"), ("TimePeriods", "s_T"))
    coverage: str                    # "complete" | "sparse"
    domain: str | None               # "fraction" | "nonnegative" | "binary"
    unit_dimension: str | None
    required_if_sets: tuple = ()
    required_if_config: dict = {}
    arc_endpoints: tuple | None = None

SHEET_REGISTRY: dict[str, SheetSpec] = { ... }
```

`required_if_config` *mirrors* (never replaces) the authors' config blocks at
`process_data.py:147-283`, so e.g. enabling `hydraulics` surfaces missing
elevation inputs before the Pyomo default of 100 m applies.

## 6. Worked examples

**Wrapper 2 / slack diagnosis** — suppose the solve is infeasible and the
harvest runs:

```text
Termination condition: infeasible
The system as built cannot satisfy all constraints. Localizing...
  CONFIRMED  Completions pad CP01 falls 12.5 kbbl short of its demand in T03
             (v_S_FracDemand[CP01, T03] = 12.5)
  CONFIRMED  Disposal site K01 is over capacity by 40.0 kbbl over the horizon
             (v_S_DisposalCapacity[K01] = 40.0)
Total shortfall cost: 52.5  (penalty-weighted, params p_psi_*)

Suggestion for CP01/T03: increase pipeline capacity into CP01 (PPA/CPA sheets),
  reduce CompletionsDemand[(CP01, T03)], or accept the shortfall explicitly.
```

The findings are physical, not mathematical: which facility, which period, by
how much, and which input tables to change.

**Wrapper 1 Part B / coverage** — blank cell in `PadRates` for (PP01, T01):

```text
Excel:      cell exists, blank            → position present, value missing
DataFrame:  cell = NaN after cleanup      → position still present (get_data.py:497-505)
stack():    .stack() DROPS NaN cells      → key DELETED (get_data.py:539)
param dict: no (PP01, T01) entry at all
Pyomo:      Param(default=0), initializer has no such key → silently 0
                                          (build_utils.py:197-211)
```

Today: no error anywhere; the model solves "optimally" for a different system.
The sets survive, so the check is: `expected − supplied` over
`ProductionPads × TimePeriods` = {(PP01, T01)}:

```text
ERROR IV-TIME-003 [input · confirmed]
  Sheet 'PadRates': no value supplied for (PP01, T01).
  Expected coverage: ProductionPads x TimePeriods (complete).
  Current behavior would silently use Pyomo default 0 → zero production.
  Suggestion: enter a finite nonnegative rate, or remove PP01 from
  ProductionPads if the pad is inactive this horizon.
```

Note the asymmetry this closes: failures that **raise** today are helped by
Part A; failures that **never raise** (this one) are only caught by Part B.

## 7. Experimental gating and maturity path

Part B checks and the two-phase harvest ship **experimental**: behind an
entry-point option, default **warn-only**, all output labeled `EXPERIMENTAL`.
Part A (catch-and-explain) and Wrapper 2's read-only classification can be on
permanently — they never change behavior.

```text
Phase 1 (now):     Part B warns only, harvest opt-in, labeled EXPERIMENTAL
Phase 2 (trust):   promote individual checks to raise (strict) as test
                   fixtures prove them out
Phase 3 (default): flip the entry-point default once every toy case passes
```

## 8. Failure classes (comment bullet 5)

| Class | Detected by | Stage | Kind | Default behavior |
|---|---|---|---|---|
| **Invalid input** | Wrapper 1 (A raises unchanged; B catches blind spots) | INPUT (pre-Pyomo) | CONFIRMED | explained; B1–B5 raise only in strict mode |
| **Infeasibility** | solver `infeasible` (authors' path) + slack harvest | INFEASIBILITY | CONFIRMED (slack > 0) / SUSPECTED (not localizable) | result stays infeasible; diagnosis printed |
| **Inconclusive solve** | Wrapper 2 termination classification | SOLVER | CONFIRMED | warning; results flagged not validated |
| **Execution / reporting error** | orchestration wrapper per stage | EXECUTION | CONFIRMED (occurred) / SUSPECTED (cause) | stage named, re-raised unchanged |
| **Suspected structure issues** | Wrapper 1 B6 | TOPOLOGY | SUSPECTED | warning, run continues |

## 9. Order of work

| # | Task | Depends on |
|---|---|---|
| 1 | `diagnostics.py` data model | — |
| 2 | `input_schema.py` registry transcription | — |
| 3 | Wrapper 1 Part B checks B1–B5, B7 | 1, 2 |
| 4 | Wrapper 1 B6 topology preflight | 1, 2 |
| 5 | Wrapper 2: termination classification + slack scan | 1 |
| 6 | Wrapper 1 Part A catch-and-explain | 1 |
| 7 | Entry-point wiring in `run_strategic_model.py` + experimental option flag | 3–6 |
| 8 | Tests + align `DIAGNOSTIC_FEEDBACK_GUIDE.md` | 3–7 |

## 10. Test plan

Extend `pareto/tests/test_utilities.py` / `test_strategic_model.py`:

1. `Diagnostic`/`ValidationReport` unit tests (add, grouping, raise behavior).
2. Registry: every sheet in `get_data()`'s recognized lists has a `SheetSpec`;
   arc sheets declare `arc_endpoints`.
3. B1: absent / empty / header-only / unparseable → right code, kind, severity.
4. B3: blank `PadRates` cell → `IV-TIME-003`; sparse sheets allow gaps with
   recorded default.
5. B2: cross-set tuple caught per-position (the union-check blind spot).
6. B4/B5: NaN/±inf rejection; fraction > 1; negative volume.
7. B6: duplicate arcs, self-loop, unreachable node (piping-only vs
   piping+trucking distinction).
8. Part A: `MissingDataError` from a broken workbook → catch-and-explain text
   produced, exception re-raised unchanged.
9. End-to-end: valid toy case produces **identical results and report** with
   and without the new layer (regression guard for additivity); failure
   workbook (`pareto/case_studies/strategic_toy_case_study_failure.xlsx`)
   produces the plain-language report.
10. Solver tests (skippable): infeasible → harvest findings;
    `maxTimeLimit` → inconclusive classification.

## 11. Out of scope

- Any modification of authors' code: `is_feasible()`,
  `model_infeasibility_detection()`, `solve_model()`, `check_required_data()`,
  `process_data.py` config blocks, `build_utils.py` slack definitions.
- Operational model parity.
- Replacing the hand-written config blocks or `_check_optional_data()` with
  registry rules (the registry only mirrors them).
- Full declarative schema / provenance manifests / `error_on_default` as a
  library-wide default.
- `exec()`-free unit handling; min-cut analysis; result-workbook validation
  sheets.

## 12. Review checklist

- [ ] `git diff` shows changes ONLY in `run_strategic_model.py` + new files.
- [ ] Baseline regression: valid toy case identical with and without the layer.
- [ ] Part B default is warn-only and output is labeled `EXPERIMENTAL`.
- [ ] Part A re-raises the authors' exceptions unchanged, verbatim message
      preserved.
- [ ] Harvest result never replaces the authoritative infeasible result.
- [ ] Every diagnostic carries severity, kind, stage, code, affected, inputs,
      suggestion.
- [ ] CONFIRMED/SUSPECTED boundary matches §4 (anchored → CONFIRMED;
      unanchored → SUSPECTED, never raises).
- [ ] Plain-language output names concrete facilities, arcs, sheets, periods.
- [ ] Tests pass: `pytest pareto/tests/`.
