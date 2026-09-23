# CFD-Memo

CFD-Memo is a Python research prototype for planning, generating, validating,
running, diagnosing, and recording OpenFOAM simulations. The current baseline
supports one real scenario: two-dimensional incompressible laminar flow around
a cylinder using Foundation OpenFOAM 10.

Implementation, tests, schemas, templates, and detailed usage are under
[`cfd-memo-agent/`](cfd-memo-agent/). The current roadmap is
[`CFD-Agent-Roadmap.md`](CFD-Agent-Roadmap.md).

## Quick Check

```powershell
cd cfd-memo-agent
.\.venv\Scripts\python.exe -m pytest -q
```

The verified engineering path is `blockMesh -> checkMesh -> icoFoam` through
Ubuntu 22.04 on WSL2. Engineering completion does not imply physical validation;
force coefficients and mesh/time-step independence remain future work.

## Privacy

Sensitive research materials under `科研立项/`, local Python environments,
generated CFD runs, Ubuntu/OpenFOAM installations, and machine-local caches are
ignored. Do not force-add them or upload credentials and personal information.
