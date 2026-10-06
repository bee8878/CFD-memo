# CFD-Memo Cross-Task Evaluation v1

This directory is a sanitized research artifact. It does not contain API keys, local
paths, raw model responses, OpenFOAM field files, or private project documents.

## Contents

- `manifest.json`: frozen software, environment, model, and protocol metadata.
- `summary.json`: aggregate and paired M3 statistics.
- `failure-cases.json`: eight safe-stop cases without local paths.
- `memory-evidence.json`: task-level effective memory actions and preventions.
- `analysis-report.md`: human-readable statistical report.

## Main Result

All groups completed 3/5 engineering tasks. CFD-Memo reduced mean corrections from 0.6 to 0.2, but the first-pass paired test was p=0.500. This small study does not establish statistical significance or physical generality.

## Reproduce

From the project root, create a Python 3.12 environment, install the project in editable
mode, run `python -m pytest -q`, audit the frozen protocol with
`python -m cfd_memo_agent.cli benchmark audit`, then analyze a completed local study
with `python -m cfd_memo_agent.cli benchmark analyze --study <study-dir>`.

Source bundle SHA-256: `0091be03d9c372931f1d78985f39bd87b1c72c7b82dc7b73462b689add07b313`.

OpenFOAM execution requires Foundation OpenFOAM 10. Raw studies remain local and are
excluded by `.gitignore`. Engineering completion is not physical validation.

This repository currently has no explicit open-source license. The artifact documents
the experiment but does not grant reuse rights until the maintainer chooses a license.
