# Banking Incident AIOps

AI capstone project: a multi-agent system that investigates banking incidents across telemetry, the EventHub (Kafka) platform and customer complaints, and returns an evidence-backed recommendation for human review.

Status: Phase 0 complete on the development laptop (libraries installed, smoke test 10/10). Next: repeat the smoke test in Vocareum, then Phase 1.

## Phase 0 smoke test

```
.venv\Scripts\python.exe scripts\smoke_test.py      # Windows
.venv/bin/python scripts/smoke_test.py              # Linux / Vocareum
```

Needs a `.env` file (copy `.env.example`). Prints PASS or FAIL for each check; keys are never printed.

## Documents

| Document | Path |
|---|---|
| Requirements specification | [docs/AI_Capstone_Project_Requirements_v0.9.md](docs/AI_Capstone_Project_Requirements_v0.9.md) |
| Architecture specification | [docs/AI_Capstone_Project_Architecture_Specification_v1.3.md](docs/AI_Capstone_Project_Architecture_Specification_v1.3.md) |
| Architecture diagrams (Mermaid sources and PNG exports) | [docs/diagrams/](docs/diagrams/) |

**Document versions:** the specifications in `docs/` are the working copies. A change that alters a document increments its version, and the file is renamed to match (for example `..._v0.8.md` becomes `..._v0.9.md`), with an entry in the document's version history. Earlier versions stay available in git history.

## Environment

- Python 3.10 (matches the Vocareum lab environment)
- Dependencies are pinned in `deployment/requirements.txt`, the only requirements file

Setup and run instructions will be added as the code phases are completed.
