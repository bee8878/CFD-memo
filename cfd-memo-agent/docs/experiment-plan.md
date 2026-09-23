# Stage B Experiment Plan

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

Use `cases/templates/cylinder-2d/` as the readable baseline case. If OpenFOAM is unavailable, use sample logs under `cases/runs/sample-logs/` to develop the runner, diagnoser, and memory-writing workflow.

## Comparison Groups

- No-memory agent: starts every task without reading prior episodes.
- Simple-cache agent: reuses only a complete prior case or fixed template.
- CFD-Memo agent: reuses structured experience, failure cases, and correction strategies, then writes a new episode after each task.

