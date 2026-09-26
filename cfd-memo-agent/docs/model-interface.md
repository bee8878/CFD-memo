# D1 Model Interface

D1 adds a provider-neutral structured-output boundary. The existing rule planner remains the
default and does not require network access. `CFD_MEMO_MODEL_PROVIDER=openai` or
`deepseek` selects an allowlisted official Responses API adapter. D2 connects the selected
adapter to the Planner Agent.

Configuration is read from process environment variables:

| Variable | Purpose |
| --- | --- |
| `CFD_MEMO_MODEL_PROVIDER` | `rules`, `openai`, or `deepseek`; defaults to `rules` |
| `CFD_MEMO_MODEL` | Explicit model ID required for a model provider |
| `OPENAI_API_KEY` | API credential; presence is reported but its value is never serialized |
| `DEEPSEEK_API_KEY` | DeepSeek credential; presence is reported but its value is never serialized |
| `CFD_MEMO_MODEL_TIMEOUT` | Request timeout in seconds; default `60` |
| `CFD_MEMO_MODEL_MAX_OUTPUT_TOKENS` | Output limit; default `2000` |

Copy `.env.example` to a local ignored `.env` in the project root. The project loads this
exact path with UTF-8 encoding, even when the CLI starts in another working directory.
Existing process environment variables take precedence over file values. No credential
belongs in task, episode, report, log, `.env.example`, or Git history.

`ModelRequest` carries the Agent role, prompt version, input text, and output JSON Schema.
Every provider returns `ModelResult(output, trace)`. The trace contains provider, model,
response ID, duration, token usage, and prompt version. The adapter requests Structured
Outputs and validates the returned object locally again. Transport errors, refusals, malformed
JSON, and schema violations have separate exceptions so the future orchestrator can stop or
fall back without treating model text as verified CFD evidence.

## D2 planner behavior

`orchestrator.plan_description()` owns provider selection and returns shared serializable
state. In OpenAI or DeepSeek mode, `PlannerAgent` requests one of two outcomes:

- `ready`: a complete candidate task plus explicit assumptions.
- `needs_clarification`: no task and at least one concrete question.

Every ready model task passes the repository task schema and deterministic CFD validation
before generation. API, refusal, malformed-output, and physics-validation failures stop by
default. Rule fallback must be requested explicitly and is recorded as `rules_fallback`.
The workflow saves `planning.json` and copies redacted planning metadata into the episode.

## D3 Case Writer behavior

After task validation, `orchestrator.prepare_case_writing()` asks the Case Writer Agent for a
bounded configuration intent. The intent names the fixed cylinder template and maps approved
task fields to an allowlisted set of OpenFOAM files. A SHA-256 task fingerprint binds the intent
to the exact validated task; stale or altered intents are rejected before a run directory is
created.

The Agent never returns file contents or commands. `generate_case()` remains the deterministic
writer and `validator` remains the authority on the resulting case. The workflow stores the
decision and redacted model trace in `case-writing.json`, `episode.json`, and the report. Rule
mode produces the same intent locally without a network request. Model failure stops by default;
the existing explicit rules fallback also applies to Case Writer.

## D4 Reviewer behavior

The Reviewer receives only structured task and validator/runner evidence after each workflow
round. A dynamic JSON Schema locks the executable decision and repair scope to the deterministic
evidence: completed rounds without findings may be accepted; supported rule candidates may be
repaired; unknown, manual, timeout, and environment failures stop. Runtime blockers remain visible,
but `MESH_NOT_VERIFIED` does not veto an explicitly simulated configuration repair.

The model explains root cause, recommendation, applicability, and limitations. It cannot name
arbitrary files or mutate a case. `propose_repairs()` independently checks the current case against
the trusted reference, the orchestrator enforces the correction budget, and every change enters a
new validation round. Each decision is saved as `rounds/round-NNN/review.json` with a redacted trace.

DeepSeek uses the official `https://api.deepseek.com/responses` endpoint and defaults to
`reasoning.effort=none` for this constrained planning task. Its trace records cached input
and reasoning token counts when returned by the API. The recommended starting model is
`deepseek-flash`. The project does not accept arbitrary proxy URLs.
