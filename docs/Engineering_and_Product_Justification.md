# Engineering and Product Justification: EventHub AIOps

**AI Capstone Project · Scenario 2: Banking · Track A (LangChain + LangGraph)**

**Companion documents:**
- [Problem framing](Problem_Framing_Document.md)
- [Evaluation report](Evaluation_Report.md)
- [Requirements v0.18](AI_Capstone_Project_Requirements_v0.18.md)
- [Architecture specification v1.12](AI_Capstone_Project_Architecture_Specification_v1.12.md)

## 1. The product decision: decision support, not automation

**Who it serves:** the on-call engineer, the Incident Escalation Team and Production Support during a
bank incident. Their bottleneck is not acting but *understanding*. The evidence sits in six systems
(logs, Kafka, API, database and network metrics, deployments) plus customer complaints, and it has to be
correlated by hand.

**What it does:** the product gives them, within about a minute:
- a correlated timeline
- ranked root-cause hypotheses, each linked to evidence
- the right runbook steps
- a page or ticket to the right team

**Why advisory only:** the system never acts on its own. Three reasons:
1. **Accountability:** in a bank, moving money, approving exceptions or changing production stay with an
   accountable person. Scenario 2 requires refusing them.
2. **Risk asymmetry:** a wrong recommendation that a human rejects costs a minute. A wrong automatic
   rollback or reversal costs money and trust.
3. **Learning:** every human decision (approve, edit, reject, rate) becomes training signal, so keeping
   the human in the loop is also how the product improves.

**Scope, deliberately small:**
- **Data:** five synthetic incident scenarios, one incident at a time.
- **Inputs:** a snapshot replay rather than live streams.
- **Dispatch:** simulated paging, ITSM and notifications, with the same interfaces a real integration
  would use.

This keeps the effort on what the brief evaluates (reliability, explainability, safety and usefulness)
rather than on integrations.

## 2. Architecture in one paragraph

Five stages: **Detect → Triage → Analyse → Respond → Close and learn**. The guiding rule is *agents
analyse, rules decide, humans approve*.
- **Deterministic services** handle detection, alert correlation and deduplication, PII redaction, the
  severity rules, the critical fast-path page and dispatch.
- **Four narrow LLM agents** do the analysis: Triage, Root Cause Analysis, Change Correlation and
  Recommendation.
- **A LangGraph state graph** of 22 nodes orchestrates them, running RCA and Change Correlation in
  parallel.
- **Read-only tools** are served to the agents over MCP. Runbooks, postmortems and verified past fixes
  are retrieved from a FAISS index.
- **A Streamlit UI** carries every human step. A learning loop turns validated feedback into prompt
  changes that a human must approve and a regression check must pass.

## 3. Key design decisions

| # | Decision | Why | Alternative rejected | Trade-off accepted |
|---|---|---|---|---|
| D1 | **Severity and paging are rules, not LLM output** (YAML thresholds, re-scored raise-only) | Critical incidents must be paged in seconds and even when the LLM is down; the decision must be explainable and auditable | Let the Triage Agent decide severity | Rules need SME-maintained thresholds; the LLM's view is used only to *raise* severity from cited evidence. **Result:** S1 page 0.1–0.4 s after creation, before any LLM call; 96% severity accuracy |
| D2 | **Four narrow agents instead of one large agent** | Each has a short prompt, its own tool set and its own metric, so failures can be located (e.g. "the RCA Agent names symptoms") and fixed separately. Change correlation runs in parallel with RCA | One agent with all 13 tools | More LLM calls (6 per run) and more code. Latency is 43–49 s against a 30 s target, the main cost of this choice (planned fix: more parallelism) |
| D3 | **LangGraph for orchestration, LangChain for model and tool binding** (Track A) | The workflow is not a free-form agent loop: it has a fast path, an LLM-down fallback, a confidence gate, bounded retries per node and a `SYSTEM_ERROR` end state. An explicit `StateGraph` makes these structural, testable and visible node by node in the UI and LangFuse | CrewAI (role-based conversation, less control over branching and failure paths); Flowise (cannot run in Vocareum); framework-free (would re-implement state, retries, parallel branches and tracing) | Learning curve and pinned versions (LangGraph 1.2.13, LangChain 1.4.3/core 1.6.6), kept stable through Python 3.10's end of life |
| D4 | **Tools are read-only, pre-aggregated, and served over MCP**; dispatch tools are not on the MCP server | The model never sees raw telemetry (cost, context, PII), only summaries with evidence IDs. The MCP server's tool list is itself a safety boundary: it contains nothing that can page or ticket. Per-agent allow-lists, an 8-call budget and duplicate-call skipping stop misuse and loops | Let agents query raw files or call dispatch directly | An MCP subprocess adds startup time and a failure mode; an in-process fallback covers it |
| D5 | **Local FAISS index** with heading-aware chunks (800 characters, 100 overlap) and `text-embedding-3-small` | 29 documents (155 chunks) need no database server; the index is reproducible and works offline in tests. Chunks follow runbook sections, so citations read "RB-NET-004 §4" | A hosted vector DB or Chroma | No incremental multi-user indexing; acceptable at this size. Only SME-verified resolutions are indexed, so the system never learns a wrong fix |
| D6 | **Structured output through function calling, with tolerant parsing** | Strict JSON-schema mode made gpt-4o-mini emit runaway output in the smoke test; function calling with a token cap is stable. Real outputs showed models filling numeric fields with `""` and lists with strings, so parsing reads what it can instead of failing the run | Strict schema mode; free text | More validation code. It fixed a real failure: 2 of 6 cases ended `SYSTEM_ERROR` before the fix, 0 after |
| D7 | **gpt-4o-mini for agents and judge**, through the course's gateway | About $0.004 per investigation against a $0.15 cap; the same `.env` switch works with a personal key | A larger model | Lower root-cause accuracy (25% top-1) and a judge from the same model family, whose scores vary between runs. Both are recorded as limitations, not hidden |
| D8 | **Learning at the prompt level, human-gated** ("RLHF without fine-tuning") | Ratings and blind pairwise preferences give a reward per prompt version. Adaptations can only add guidelines, examples or retrieval aliases. Each needs SME validation of the feedback, at least 2 signals with no more than 30% from one reviewer, a human approval, and a full golden-set check (no metric down more than 5 points) | Fine-tuning; automatic prompt optimisation | Slower learning; needs reviewers. **Evidence it works as a safeguard:** the first adaptation made root-cause accuracy worse and was reverted automatically, with the reason recorded |
| D9 | **Explicit memory layers** | Working state is discarded after each run, so nothing leaks between incidents. Long-term memory is separate and durable: knowledge base, verified fixes, prompt versions, golden set, preference store. Vocareum sessions are ephemeral, so long-term stores are committed to git | Conversational memory across incidents | No chat-style memory (out of scope); "memory" is the incident store (deduplicated repeat alerts), verified-fix retrieval and the golden set |
| D10 | **Evaluation built in, not bolted on** | A golden dataset (5 scenarios × 5 variants, growing with SME promotions), programmatic checks, DeepEval metrics with a function-calling judge, fixtures for failure paths, LangFuse traces and scores. Evaluation runs use their own store and dispatch nothing | Manual spot checks | About $0.25 and 12 minutes per full judged run. It found two real bugs and the main accuracy gap |

## 4. Is the complexity justified?

The brief rewards engineering judgment, not complexity. Every component traces to a requirement or a
measured need:

| Component | Why it is needed |
|---|---|
| Deterministic intake and rules | Speed and LLM-independence of critical pages (Scenario 2: escalate high-risk cases) |
| Narrow agents | Locating and fixing failures; the evaluation could say *which* agent was wrong |
| LangGraph | The failure paths (fast path, fallback, `SYSTEM_ERROR`) are the core of reliability |
| MCP and allow-lists | Tool safety and the "show incorrect tool use" requirement |
| The learning loop | The adaptation requirement, with the human gates Scenario 2 implies |

**Deliberately left out:**
- auto-remediation
- live streaming ingestion
- real paging and ticketing integrations
- model fine-tuning (a DPO export exists for later)
- cross-incident chat memory
- authentication: roles are chosen in the sidebar and recorded with every decision

**Where the complexity cost shows:** latency, at 43–49 s. The evaluation report lists the fix.

## 5. Safety approach: layered, deterministic where it matters

| Layer | Controls | Evidence |
|---|---|---|
| **Input** | Out-of-scope requests (money movement, approvals, legal advice, data changes or actions) refused before any incident or LLM; PII and secrets redacted at intake; prompt-injection detection; untrusted data wrapped in tags | 4 refusal categories, 23 tests, screenshot; injection fixture passes |
| **Tools** | Read-only tools only; per-agent allow-lists; argument validation; tool budget and duplicate-call skip; dispatch tools reserved for workflow nodes | Forced demo plus a real corrected model mistake |
| **Output** | Every claim must cite evidence IDs; uncited claims and actions removed; action policy (deny-list, risk levels, two-step confirmation for high-risk steps); tone check; PII re-check | Citation validity 100%; without retrieval, every fix action is dropped as uncited |
| **Dispatch** | Severity by rules; one page per incident (dedup); everything labelled SIMULATED | Alert-storm fixture: 1 incident, 1 page |
| **Learning** | SME validation, reviewer cap, human approval, golden-set regression check, scope limits (never rules, thresholds or the model) | ADP-001 reverted; ADP-002 rejected with a reason |
| **Logs** | Structured JSON logs, redacted at write time; refusals log the category only | 0 of 131 source personal values in 10,223 log lines |

**Known gap:** a missing data source does not yet lower the agent's confidence (0 of 5 missing-source
cases abstained). The planned fix is a deterministic confidence cap, in code rather than in a prompt, so
it holds whatever the model answers.

## 6. Deployment assumptions and limitations

**Environments:**
- **Development:** a Windows laptop.
- **Demo:** Vocareum (Linux).
- **Both:** Python 3.10, every library pinned in `deployment/requirements.txt` (LangGraph 1.2.13, LangChain
  1.4.3, OpenAI SDK 3.24.0, FAISS 1.15.1, DeepEval 4.2.8, LangFuse 4.17.0, Streamlit 1.65.0, MCP
  1.30.0), and `scripts/setup_env.sh` in Vocareum.
- **Tests:** 466 automated offline tests pass on both, with no API cost.

| Area | Assumption | Limitation, and what production would need |
|---|---|---|
| **Keys and services** | OpenAI (Vocareum gateway via `OPENAI_BASE_URL`, or a personal key) and LangFuse Cloud keys live only in `.env`, never in code or logs | Production: a secrets manager and a private model endpoint |
| **Data** | Synthetic telemetry and documents for 5 scenarios, one incident at a time, a shared UTC clock and service names | Accuracy on real incidents is unknown; production needs live connectors and streaming detection |
| **Dispatch** | Mock pager, ITSM, chat and email write to a local outbox | Production: real integrations behind the same interfaces |
| **Users** | No authentication; the role is picked in the sidebar and recorded | Production: SSO and role-based access |
| **Durability** | Vocareum sessions are ephemeral; long-term stores are committed with `scripts/commit_state.sh` | Production: a database for the incident, feedback and adaptation stores |
| **UI** | Streamlit on port 8501, reached in Vocareum through its inbound proxy | Single-node; fine for a team, not for many concurrent users |
| **Performance and cost** | About $0.004 and 43–49 s per investigation; S1 page in under a second; gateway rate limits handled with 2 runs at a time and backoff | The 30 s target needs more parallelism |
| **Observability** | Three JSON log files, a flow log per run, LangFuse traces | The LangFuse hobby tier keeps 30 days; the logs are the durable record |
| **Platform** | Python 3.10 reaches end of life in October 2026 | Pins keep the build stable now; an upgrade is the first maintenance task |

## 7. What we would do next

**From the evaluation's root-cause analysis:**
1. **Abstain on missing evidence:** a deterministic confidence cap when a required source returns no data.
2. **Give the RCA Agent the change-correlation findings** before it ranks hypotheses. Today it cannot see
   change records, which is why it names symptoms and why the prompt-level adaptation could not help.
3. **Steadier evaluation:** repeated judged runs per version, and SME spot-checks of the judge.
4. **Latency:** parallel tool rounds to reach 30 s.
5. **Production hardening:** real connectors, authentication, a persistent store, a secrets manager.

These are changes to code and architecture, so they go through human review. The adaptation engine is
deliberately not allowed to make them.
