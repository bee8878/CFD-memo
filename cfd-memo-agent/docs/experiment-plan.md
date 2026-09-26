# Experiment Plan (Draft)

## MVP

The first CFD-Memo prototype supports one CFD case, one solver, and one complete data loop: task parsing, case generation, configuration check, run or simulated run, log diagnosis, and memory recording.

## First Case

- Case: 2D cylinder flow
- Flow model: incompressible laminar flow
- Default solver: `icoFoam`
- Validation setting: `Re=100`
- Parameter relation: `Re = U * D / nu`
- Default values: `U=1.0`, `D=1.0`, `nu=0.01`

## Metrics

- Task success rate
- Configuration generation correctness
- OpenFOAM log error classification accuracy
- Automatic correction count
- Experience reuse rate
- Failure avoidance rate
- Per-task elapsed time

## Baseline

Use `cases/templates/cylinder-2d/` as the engineering baseline. The default case has completed `blockMesh`, `checkMesh`, and `icoFoam` with Foundation OpenFOAM 10. Sample logs under `cases/runs/sample-logs/` are only for deterministic software tests and do not count as CFD results.

## Comparison Groups

- No-memory agent: starts every task without reading prior episodes.
- Simple-cache agent: reuses only a complete prior case or fixed template.
- CFD-Memo agent: reuses structured experience, failure cases, and correction strategies, then writes a new episode after each task.

## Remaining Protocol Decisions

Before the formal comparison, freeze the task list, sample size, repeat count, model and prompt version, correction budget, memory snapshot, and success thresholds. Separate experience-building tasks from evaluation tasks. Physical acceptance must include field validity and reference quantities, followed by mesh and time-step independence checks.
