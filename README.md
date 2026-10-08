# Banking Incident AIOps

AI capstone project: a multi-agent system that investigates banking incidents across telemetry, the EventHub (Kafka) platform and customer complaints, and returns an evidence-backed recommendation for human review.

Status: Phase 0 (environment, smoke test 11/11) and Phase 1 (schemas, configuration, logging) complete. Next: Phase 2 (data).

Run the tests with `.venv\Scripts\python.exe -m pytest` (Windows) or `python -m pytest` with the environment active.

## Session setup (Linux / Vocareum)

Run at the start of every session:

```
source scripts/setup_env.sh            # creates .venv if needed, installs only when requirements changed, activates
source scripts/setup_env.sh --smoke    # same, then runs the smoke test
```

## Phase 0 smoke test

```
.venv\Scripts\python.exe scripts\smoke_test.py      # Windows
.venv/bin/python scripts/smoke_test.py              # Linux / Vocareum
```

Needs a `.env` file (copy `.env.example`). Prints PASS or FAIL for each check; keys are never printed.

## Documents

| Document | Path |
|---|---|
| Requirements specification | [docs/AI_Capstone_Project_Requirements_v0.10.md](docs/AI_Capstone_Project_Requirements_v0.10.md) |
| Architecture specification | [docs/AI_Capstone_Project_Architecture_Specification_v1.4.md](docs/AI_Capstone_Project_Architecture_Specification_v1.4.md) |
| Architecture diagrams (Mermaid sources and PNG exports) | [docs/diagrams/](docs/diagrams/) |

**Document versions:** the specifications in `docs/` are the working copies. A change that alters a document increments its version, and the file is renamed to match (for example `..._v0.8.md` becomes `..._v0.9.md`), with an entry in the document's version history. Earlier versions stay available in git history.

## Environment

- Python 3.10 (matches the Vocareum lab environment)
- Dependencies are pinned in `deployment/requirements.txt`, the only requirements file

Setup and run instructions will be added as the code phases are completed.
