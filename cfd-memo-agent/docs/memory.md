# E1-E2 Structured Memory

E1 adds an opt-in local `MemoryManager`. The default remains `no_memory` so earlier
baselines and comparison groups do not silently gain historical information.

## Four Layers

- Working memory exists in one workflow process and records the current task, retrieved IDs,
  and actual uses.
- Episodic memory archives the complete final episode under `episodes/`.
- Knowledge memory stores compact repair rules under `knowledge/`; it excludes file bodies,
  logs, credentials, and research source documents.
- Procedural memory stores verified execution steps under `procedures/`.

K1 replaces the ambiguous `verified` label with four machine-derived levels:

- `candidate`: a correction was observed, but deterministic evidence is missing.
- `config_verified`: the destination round and its hashed validation report pass.
- `run_verified`: a real OpenFOAM attempt completed and its complete result index verifies.
- `physics_verified`: run evidence also carries an accepted physical validation result.

Model reflection text is never an input to the level calculation. Repeated matching experiences
merge evidence instead of creating duplicate rules. Existing v1 records are read as candidates
until they are re-extracted from their original episode because they lack artifact hashes.

## Retrieval and Agent Injection

E2 keeps case type, solver, flow model, and dimension as hard applicability filters. It then ranks
eligible records with a deterministic local hashing vector, Reynolds-number similarity, error-code
overlap, and confidence. Chinese boundary phrases and OpenFOAM file semantics are included in the
local text representation. This is reproducible lexical vector retrieval, not a hosted embedding
model and not proof of cross-geometry semantic generalization.

The same `MemoryManager` serves all three agents:

- Planner receives natural-language matches before task construction and records cited IDs.
- Case Writer receives task-filtered matches and produces a bounded `preventive_files` list.
- Reviewer receives matches for current deterministic finding codes.

If a guarded file drifts from the newly generated trusted reference before the first run, the
workflow restores only that allowed file and records `memory-prevention.json`. This can prevent a
known controlled configuration failure without allowing the model to write arbitrary content.
Current validator/runner evidence remains authoritative.

Only active records at `config_verified` or higher are retrieved. Each reuse updates
evidence-backed confidence. Successful valid outcomes raise confidence;
repeated failed reuse lowers it, and after two negative outcomes with confidence below 0.5 the
record returns to `candidate` status and is no longer retrieved.

`memory extract` rebuilds knowledge from a saved episode. `memory list` shows provenance and
trust levels. `approve/reject/enable/disable/delete` provide explicit user control. Approval does
not upgrade machine trust, while disabled or rejected records are excluded from retrieval.

```powershell
python -m cfd_memo_agent.cli run "做 Re=100 的二维圆柱绕流" `
  --runner simulated --fault missing-boundary `
  --memory-mode cfd_memo --memory-dir cases/memory
```

Run a second similar task with the same memory directory to exercise retrieval. `episode.json`
schema v3 records retrieval stages and scores, cited IDs, prevention evidence, confidence-backed
uses, newly learned IDs, procedure IDs, and the archived episode path. Local `cases/memory/` is
ignored by Git.

```powershell
python -m cfd_memo_agent.cli memory extract --episode <episode.json> --memory-dir cases/memory
python -m cfd_memo_agent.cli memory list --memory-dir cases/memory
```
