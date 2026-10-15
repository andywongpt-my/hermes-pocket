# Upstream references inspected on 2026-10-14

- FoloToy AI Passport: `a9320ab6eaa21b90759a4a22d1039092385f24dd`
  https://github.com/FoloToy/ai-passport/tree/a9320ab6eaa21b90759a4a22d1039092385f24dd
- NousResearch Hermes Agent: `0e427d788d253630d38a3a51cd76849e73cd6925`
  https://github.com/NousResearch/hermes-agent/tree/0e427d788d253630d38a3a51cd76849e73cd6925

Contract inspection: gateway/platforms/api_server.py and api_server_runs.py.
The adapter checks `features.run_submission`, `run_status`, and `run_stop`.
It submits `input` to POST /v1/runs with Idempotency-Key, reads run_id from
acceptance and polls GET /v1/runs/{id}. Result text is `output`. Upstream
`waiting_for_approval` maps to local `awaiting_approval`. Stop uses POST
/v1/runs/{id}/stop and still requires a subsequent status read.

These are inspected upstream contracts, not a compatibility certification against
the owner's installed server. The early bridge foundation did not copy upstream code. The current firmware
includes a modified FoloToy BSP; its original MIT notice is retained. Separately
audit dependencies and assets. Device firmware work must first read the pinned
FoloToy AGENTS instructions, BSP and board documentation. The README hardware
summary has differences from detailed BSP sources; resolve them before firmware.

Ollama current reference: https://github.com/ollama/ollama/blob/main/docs/openapi.yaml
contains /api/usage and /api/balance. No usage adapter is implemented in this milestone.

## Usage parser references checked 2026-10-14

- Codex: https://learn.chatgpt.com/docs/app-server, account/rateLimits/read and
  account/rateLimits/updated, usedPercent/windowDurationMins/resetsAt, including
  the multi-bucket rateLimitsByLimitId view. No Codex process is started.
- Claude Code: https://code.claude.com/docs/en/statusline, rate_limits.five_hour
  and seven_day windows. Missing windows remain absent; resets are Unix seconds.
- Ollama: https://github.com/ollama/ollama/blob/main/docs/openapi.yaml,
  BalanceResponse supports included USD credits or legacy session/weekly
  remaining percentages, plus purchased USD credits. Parser supports both shapes.

These are synthetic parser tests, not authenticated provider reads. OpenAI and
Anthropic API billing adapters remain separate future work.
