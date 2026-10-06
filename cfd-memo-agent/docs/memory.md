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
local text representation. K2 exposes this scorer through `EmbeddingProvider`: the packaged
`HashingEmbeddingProvider` remains the offline default, while `CallableEmbeddingProvider` can
adapt a local model or an explicitly configured hosted embedding function. No text is sent over
the network by default.

```python
from cfd_memo_agent.memory import CallableEmbeddingProvider
from cfd_memo_agent.workflow import run_workflow

provider = CallableEmbeddingProvider("local-model", "v1", embed=my_embed_function)
result = run_workflow(..., memory_mode="cfd_memo", memory_embedding=provider)
```

Every retrieval records the provider and version, hard-filter matches, semantic/Re/confidence
score components, matched problem codes, and short Chinese reasons. This makes ranking decisions
inspectable without exposing complete logs or case files to an embedding provider.

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

Each Agent citation starts with outcome `pending`. At workflow completion it becomes `effective`
or `ineffective` based on the deterministic workflow result. The episode keeps the exact decision
and effect, and the experience record appends the same evidence to `usage_history`; confidence is
updated from that outcome rather than from the Agent's own explanation.

`memory extract` rebuilds knowledge from a saved episode. `memory list` shows provenance and
trust levels. `approve/reject/enable/disable/delete` provide explicit user control. Approval does
not upgrade machine trust, while disabled or rejected records are excluded from retrieval.

## Conflicts, Revisions, and Transfer Acceptance

K3 assigns every experience a monotonic revision number and a hash-only revision history. Creation,
evidence merge, confidence update, user control, conflict detection, and conflict resolution each
produce a revision event. This records what kind of change occurred without copying case contents
or credentials into the knowledge record.

`memory audit` compares active verified records. If the same applicability and problem-code set
points to different repair actions, both records become `conflicted` and neither can be retrieved.
Only an explicit `memory resolve <preferred> <rejected> --note ...` choice restores the preferred
record; the rejected record is disabled and both histories retain the decision.

`memory acceptance` reads a versioned manifest of source/target episode pairs. A pair passes only
when both use the real runner, the source recovered after a correction, the tasks differ, the
target effectively reused a source experience at `run_verified` or higher, and the target required
fewer corrections. Simulated episodes fail by design.

`memory collect-transfer` creates this pair in isolated run directories. It uses task JSON and the
rules provider, so it does not call an LLM. The source intentionally damages only its fresh case
copy, records the validation failure, repairs it, and then runs real OpenFOAM. The target receives
the same controlled drift, but a retrieved experience restores the trusted file before its first
real execution. The first local acceptance passed at Re=100 -> Re=120: source corrections 1,
target corrections 0, both real runs completed, and the reused record reached `run_verified`.
This demonstrates configuration-error reuse only; `physical_validated` remains false.

```json
{
  "schema_version": 1,
  "cases": [{
    "case_id": "boundary-transfer-01",
    "source_episode": "source/episode.json",
    "target_episode": "target/episode.json"
  }]
}
```

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
python -m cfd_memo_agent.cli memory audit --memory-dir cases/memory
python -m cfd_memo_agent.cli memory acceptance --manifest acceptance.json --memory-dir cases/memory
python -m cfd_memo_agent.cli memory collect-transfer --task examples/task.cylinder-2d.json `
  --memory-dir cases/runs/k3c-memory --runs-dir cases/runs/k3c-acceptance
```
