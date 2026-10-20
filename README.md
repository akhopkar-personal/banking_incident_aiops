# EventHub AIOps

**A multi-agent decision-support system for banking incidents.** It correlates logs, Kafka events, API,
database and network metrics, deployments and customer complaints. It returns an evidence-backed root
cause, a rules-decided severity, runbook-cited actions and a simulated page or ticket. A human reviews
every recommendation, and validated feedback improves the agents under human control.

**Project:** AI Capstone Project · Scenario 2: Banking (non-transactional) · Track A: LangChain +
LangGraph.

**Stack:** Python 3.10 · LangGraph · LangChain · OpenAI gpt-4o-mini · FAISS · MCP · DeepEval · LangFuse ·
Streamlit · 466 automated tests.

## Key documents

Suggested reading order:

| # | Document | What it contains |
|---|---|---|
| 1 | [Problem Framing Document](docs/Problem_Framing_Document.md) | Users and workflow, the problem, inputs/outputs/constraints/assumptions, example questions, success criteria, known failure cases |
| 2 | [Demo Script](docs/Demo_Script.md) | Five forced interactions with expected output and evidence: an incident end to end, safety refusals, clarification, tool use, feedback to adaptation |
| 3 | [Evaluation Report](docs/Evaluation_Report.md) | Results against targets, consistency, prompt and retrieval comparisons, safety and ethics review, failure analysis with root cause, fix and before/after proof, improvements |
| 4 | [Engineering & Product Justification](docs/Engineering_and_Product_Justification.md) | Product decision, ten design decisions with trade-offs, complexity check, safety approach, deployment assumptions and limitations |
| 5 | [Evidence Map](docs/evidence/EVIDENCE_MAP.md) | Which evidence proves which requirement: the log file, event, run ID, flow log and screenshot for each |
| 6 | [Demo deck (PDF)](docs/EventHub_AIOps_Demo_v4.2.pdf) | The presentation: architecture, agents, sequence flow, evaluation, safety, the learning loop |
| 7 | [Requirements Specification v0.18](docs/AI_Capstone_Project_Requirements_v0.18.md) | Full requirements: scope, personas, functional requirements, data, metrics and targets, guardrails |
| 8 | [Architecture Specification v1.12](docs/AI_Capstone_Project_Architecture_Specification_v1.12.md) | Contracts behind the code: schemas, LangGraph wiring, agents, tools, retrieval, safety, evaluation |
| — | [Setup and run guide](SETUP.md) | Install on Windows or Vocareum, `.env`, everyday commands, troubleshooting |

## Where each capstone requirement is shown

| Requirement (submission guidelines) | Where |
|---|---|
| Agent source with run instructions | This repository; [SETUP.md](SETUP.md) |
| Problem framing (1–2 pages) | [docs/Problem_Framing_Document.md](docs/Problem_Framing_Document.md) |
| Demo script (3–5 forced interactions) plus evidence | [docs/Demo_Script.md](docs/Demo_Script.md), [docs/evidence/screenshots/](docs/evidence/screenshots/) |
| Prompt comparison table (same test set, 2–3 variants) | [docs/evidence/prompt_comparison.md](docs/evidence/prompt_comparison.md); summary in the Evaluation Report, Section 5 |
| Evaluation report with a debugged failure (root cause, fix, before/after) | [docs/Evaluation_Report.md](docs/Evaluation_Report.md), Section 9 |
| Engineering & product justification | [docs/Engineering_and_Product_Justification.md](docs/Engineering_and_Product_Justification.md) |
| Retrieval, tool usage, memory, adaptation, safety proof | [docs/evidence/EVIDENCE_MAP.md](docs/evidence/EVIDENCE_MAP.md), Sections 5 to 9 and 13 |
| Framework usage (Track A) | Justification, decision D3; [src/agent/core_agent.py](src/agent/core_agent.py) (the LangGraph `StateGraph`) |

## Evidence

[docs/evidence/](docs/evidence/) holds:

| Item | Contents |
|---|---|
| `runs/` | Console output of each evidence run |
| `flowlogs/` | Every log line of the key runs, by stage |
| `screenshots/` | UI and LangFuse screenshots |
| `prompt_comparison.md`, `retrieval_comparison.md`, `adp001_before_after.md` | The comparison reports |
| `ADP-001.md` | The adaptation evidence report |

The raw records behind them are in `logs/` (interactions, error and eval logs), `data/evaluation/`,
`data/incident_state/` and `data/feedback/`. These are runtime data, so they are not in git; they are
included in the submission zip.

## Run it

```bash
source scripts/setup_env.sh          # Vocareum (Windows: see SETUP.md, section 2)
python -m pytest                     # 466 offline tests, no API cost
streamlit run src/ui/app.py          # the UI on port 8501
```

Keys go in `.env` (see [SETUP.md](SETUP.md), section 4); they are never committed.

## Repository layout

| Folder | Contents |
|---|---|
| `src/` | The system: `agent/` (LangGraph workflow and the four agents), `services/` (detection, correlation, severity rules, gates, flow log), `tools/` and `mcp_server/`, `tool_retrieval/` (FAISS), `safety/`, `feedback/`, `evaluation/`, `ui/` (Streamlit) |
| `data/` | Synthetic telemetry for 5 scenarios and 4 fixtures, the golden dataset, severity rules, action policy, prompt versions |
| `knowledge/` | Runbooks, postmortems, regulatory notes and verified resolutions for retrieval |
| `scripts/` | Investigation, evaluation, comparison, flow log, demo and setup scripts |
| `tests/` | Offline test suite with a scripted model |
| `docs/` | The documents above, diagrams and evidence |
| `deployment/` | Pinned `requirements.txt` |
