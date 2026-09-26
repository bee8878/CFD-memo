# C6 Physical Validation Protocol

## Scope

This protocol validates only the two-dimensional incompressible laminar cylinder case at
`Re=100`, `U=1 m/s`, and `D=1 m`. Command completion and finite `U/p` fields remain
engineering evidence; physical acceptance additionally requires stable force coefficients,
published-reference agreement, and mesh/time-step independence.

The physical baseline places every outer boundary `30D` from the cylinder. This follows the
paper's structured O-grid study and is compatible with the current symmetric eight-sector
topology. It removes the 10% blockage of the earlier engineering-only domain. Radial grading
is increased to 100 so the larger far field does not coarsen the cylinder-adjacent cells.

## Measured Quantities

OpenFOAM 10 `forceCoeffs` integrates the `cylinder` patch. The two-dimensional mesh span is
`0.1 m`, so `Aref=D*span`; `lRef=D`, `magUInf=U`, and `rhoInf=1`. The runner reads the
second half of the signal and records mean `Cd`, `Cl` amplitude and RMS, complete lift
periods, period variation, and `St=fD/U`. At least five complete periods with period
coefficient of variation no greater than 10% are required before reference comparison.

## Reference Gate

The initial acceptance bands are `Cd=1.30–1.40`, lift amplitude `0.20–0.36`, and
`St=0.155–0.175`. They cover the Re=100 numerical values collected in Tables 2 and 3 of
[Harichandan and Roy (2012)](https://www.jafmonline.net/article_1329_01e9565cecc4e989123f9620c1d09c09.pdf).
These bands are fixed before the long run and must not be tuned to the project output.

## Run Matrix

Use `endTime=100` and analyze the second half of each run. The medium baseline is shared:

| Purpose | target_cells | deltaT |
| --- | ---: | ---: |
| Mesh coarse | 5000 | 0.005 |
| Baseline / mesh medium | 10000 | 0.005 |
| Mesh fine | 20000 | 0.005 |
| Mesh extra-fine | 40000 | 0.005 |
| Wake-focused fine | 29440 | 0.005 |
| Wake-focused extra-fine | 47200 | 0.005 |
| Time-step coarse | 10000 | 0.01 |
| Time-step fine | 10000 | 0.0025 |

The wake-focused extra-fine-versus-fine relative changes in mean `Cd` and `St` must each be
no more than 3%; the `Cl` amplitude change must be no more than 5%. The wake-focused
extra-fine mesh is the candidate used for reference comparison. All eight runs must complete with fresh mesh, field, and force
evidence. Until every gate passes, `physical_validated` remains `false`.

The original one-ring 20000-to-40328-cell comparison failed the lift gate at 7.27%.
The replacement two-ring topology separates the cylinder-to-10D near field from the far
field and allocates extra angular cells to the downstream sectors. Its 29440-to-47200-cell
changes are 0.088% for `Cd`, 0.727% for `St`, and 1.83% for lift amplitude. The final
`Cd=1.32566`, lift amplitude `0.33536`, and `St=0.16309` pass the fixed reference bands;
the aggregate study therefore records `physical_validated=true` for this benchmark only.
