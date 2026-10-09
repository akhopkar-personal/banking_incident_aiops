# Banking Incident AIOps

AI capstone project: a multi-agent system that investigates banking incidents across telemetry, the EventHub (Kafka) platform and customer complaints, and returns an evidence-backed recommendation for human review.

**Status:** Phase 0 (environment, smoke test 11/11) and Phase 1 (schemas, configuration, logging) are complete. Next: Phase 2 (data).

**Contents**

1. [What you need before you start](#1-what-you-need-before-you-start)
2. [Setup on Windows (development laptop)](#2-setup-on-windows-development-laptop)
3. [Setup in Vocareum (demo environment)](#3-setup-in-vocareum-demo-environment)
4. [The `.env` file](#4-the-env-file)
5. [Everyday commands](#5-everyday-commands)
6. [Troubleshooting](#6-troubleshooting)
7. [Documents](#7-documents)

---

## 1. What you need before you start

| Item | Where to get it | Notes |
|---|---|---|
| **OpenAI API key** | The course's Vocareum lab provides a gateway key (starts with `voc-`) | A `voc-` key works only through the gateway URL `https://openai.vocareum.com/v1`. It works both in Vocareum and on a laptop. A personal OpenAI key (`sk-...`) also works, with `OPENAI_BASE_URL=https://api.openai.com/v1` |
| **LangFuse Cloud project** | [cloud.langfuse.com](https://cloud.langfuse.com), then **Settings, API Keys** | You need the public key (`pk-lf-...`) and secret key (`sk-lf-...`). Note the project's region: EU uses `https://cloud.langfuse.com`, US uses `https://us.cloud.langfuse.com` |
| **Repository access** | GitHub: `https://github.com/akhopkar-personal/banking_incident_aiops` (private) | Ask the owner to add you as a collaborator. Until the repository is pushed, the code is shared as a zip file (`banking_incident_aiops_phase0.zip`) |

**Keep keys private.** Keys go only into your local `.env` file, which git ignores. Never paste a key into a chat, an issue, a commit or a shared document. If a key is exposed, rotate it immediately (create a new key, delete the old one).

---

## 2. Setup on Windows (development laptop)

Run the commands below in **PowerShell** (for example a VS Code terminal).

### 2.1 Install Git and Python 3.10 (once per machine)

The project uses **Python 3.10** so that the laptop and Vocareum run the same version; the library versions in `deployment/requirements.txt` were chosen and tested for 3.10.

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.10 -e
```

Then **close and reopen VS Code** (all windows) so its terminals see the new programs. Check:

```powershell
git --version
py -3.10 --version        # should print Python 3.10.x
```

If you prefer installers: Git from [git-scm.com](https://git-scm.com/download/win), and Python 3.10.11 (the last Windows installer for 3.10) from [python.org](https://www.python.org/downloads/release/python-31011/). Do not use the Microsoft Store "python" shortcut.

### 2.2 Get the code

With repository access:

```powershell
cd "D:\AI_Course\Capstone Project"          # any folder you like
git clone https://github.com/akhopkar-personal/banking_incident_aiops.git
cd banking_incident_aiops
```

The first `git clone` or `git push` opens a browser window to sign in to GitHub.

Without repository access: unzip `banking_incident_aiops_phase0.zip` into a folder of your choice and `cd` into the `banking_incident_aiops` folder.

### 2.3 Create the virtual environment and install the libraries

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r deployment\requirements.txt
pip check
```

- The install takes about 10 minutes the first time.
- `pip check` must print `No broken requirements found.`
- If `Activate.ps1` is refused with "running scripts is disabled", run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then activate again.
- After activation the prompt starts with `(.venv)`. Each new terminal needs activation again, unless VS Code does it for you (next step).

### 2.4 Point VS Code at the environment

1. **File, Open Folder**, and open the `banking_incident_aiops` folder itself.
2. Install the **Python** extension from Microsoft if you don't have it.
3. Press **Ctrl+Shift+P**, run **Python: Select Interpreter**, and choose the entry marked `.venv` (Python 3.10).

New VS Code terminals now activate `.venv` automatically.

### 2.5 Create your `.env` file

```powershell
Copy-Item .env.example .env
```

Open `.env` and fill in the keys (see [Section 4](#4-the-env-file)). Tip: close the file, or click elsewhere, before chatting with an AI assistant in VS Code, because selected text can be sent along with your message.

### 2.6 Check that everything works

```powershell
python scripts\smoke_test.py      # 11 checks; each prints PASS or FAIL. Makes a few small API calls (well under $0.01)
python -m pytest                  # unit tests; no API calls
```

Expected: `11 passed, 0 failed` from the smoke test, and all unit tests passing. The smoke test prints a LangFuse trace ID; you can find that trace in the LangFuse web UI.

---

## 3. Setup in Vocareum (demo environment)

Vocareum is Linux with Python 3.10.2. Use the Jupyter terminal.

### 3.1 First-time setup

**Get the code** (choose one):

- **Zip:** upload `banking_incident_aiops_phase0.zip` with the Jupyter **Upload** button (into a folder that the lab keeps between sessions, if it has one), then:
  ```bash
  python3 -m zipfile -e banking_incident_aiops_phase0.zip .
  cd banking_incident_aiops
  ```
- **Git** (once the repository is pushed): cloning a private repository needs a GitHub personal access token as the password, or an SSH key added to your GitHub account.
  ```bash
  git clone https://github.com/akhopkar-personal/banking_incident_aiops.git
  cd banking_incident_aiops
  ```

**Set up the environment and `.env`:**

```bash
source scripts/setup_env.sh
```

The first run creates `.venv`, installs the libraries (several minutes) and creates `.env` from `.env.example`. Open `.env` in the Jupyter editor and fill in the keys (see [Section 4](#4-the-env-file)). Then:

```bash
source scripts/setup_env.sh --smoke    # expected: 11 passed, 0 failed
```

### 3.2 Every new session

```bash
cd banking_incident_aiops
source scripts/setup_env.sh
```

`setup_env.sh` is safe to run every time. It:

- creates `.venv` only if it is missing or broken (for example after Vocareum updated its Python);
- reinstalls libraries only when `deployment/requirements.txt` has changed since the last install;
- activates the environment in your terminal (use `source`, not `bash`, so it stays active);
- warns if keys in `.env` are empty, without printing any values.

If the lab was reset and the folder is gone, repeat [3.1](#31-first-time-setup).

### 3.3 Updating to a newer version

- **Git:** `git pull`, then `source scripts/setup_env.sh`.
- **Zip:** upload the new zip and unzip it over the existing folder, from the folder that *contains* `banking_incident_aiops`:
  ```bash
  python3 -m zipfile -e banking_incident_aiops_phase0.zip .
  cd banking_incident_aiops
  source scripts/setup_env.sh --smoke
  ```
  Unzipping overwrites changed files and keeps your `.env` and `.venv`. It never deletes files, so files that were renamed or removed in the new version must be deleted by hand; each update's notes list them.

### 3.4 Saving long-term data

Vocareum sessions can be reset. Before a session ends, anything the project should remember (the `data/` folder, verified resolutions, logs) must be committed to git (`scripts/commit_state.sh`, added in a later phase) or downloaded (Req. Section 10.3.4).

---

## 4. The `.env` file

Copy `.env.example` to `.env` and fill it in. Never commit `.env`; git already ignores it.

| Setting | Value | Required |
|---|---|---|
| `OPENAI_API_KEY` | Your `voc-...` gateway key, or a personal `sk-...` key | Yes |
| `OPENAI_BASE_URL` | `https://openai.vocareum.com/v1` for a `voc-` key; `https://api.openai.com/v1` for a personal key | Yes |
| `LANGFUSE_PUBLIC_KEY` | `pk-lf-...` | Yes (the app still runs without LangFuse, but tracing is off) |
| `LANGFUSE_SECRET_KEY` | `sk-lf-...` | Yes (as above) |
| `LANGFUSE_HOST` | `https://cloud.langfuse.com` (EU) or `https://us.cloud.langfuse.com` (US) | Yes |
| `LLM_MODEL` | `gpt-4o-mini` | Default is fine |
| `JUDGE_MODEL` | Empty means the same as `LLM_MODEL` | Optional |
| `EMBEDDING_MODEL` | `text-embedding-3-small` | Default is fine |
| `LLM_ENABLED` | `true`; `false` runs the rules-only mode (kill switch) | Default is fine |
| `TOOL_TRANSPORT` | `mcp` (agents use tools through the MCP server) or `inprocess` | Default is fine |
| `LOG_LEVEL` | `INFO` | Default is fine |

---

## 5. Everyday commands

Run from the `banking_incident_aiops` folder with the environment active.

| Task | Windows (PowerShell) | Vocareum (bash) |
|---|---|---|
| Activate the environment | `.\.venv\Scripts\Activate.ps1` (or let VS Code do it) | `source scripts/setup_env.sh` |
| Unit tests | `python -m pytest` | `python -m pytest` |
| Smoke test | `python scripts\smoke_test.py` | `python scripts/smoke_test.py` |
| Leave the environment | `deactivate` | `deactivate` |

---

## 6. Troubleshooting

| Problem | Cause and fix |
|---|---|
| `python` opens the Microsoft Store, or "Python was not found" | Windows' Store shortcut. Use `py -3.10`, or activate `.venv` first |
| `git` or `py` "is not recognized" right after installing | Terminals opened before the install don't see it. Close and reopen VS Code (all windows) |
| "running scripts is disabled" when activating | `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, once |
| `ModuleNotFoundError` (for example `langgraph`) | The environment isn't active, or the wrong interpreter is selected. Activate `.venv` / select it in VS Code |
| Smoke test: OpenAI `401 invalid_api_key` with a `voc-` key | `OPENAI_BASE_URL` is missing or wrong; set it to `https://openai.vocareum.com/v1` |
| Smoke test: LangFuse check fails with 401 | Wrong keys, or the keys belong to the other region; check `LANGFUSE_HOST` |
| Vocareum: `python3 -m venv` fails mentioning `ensurepip` | The lab image lacks the venv module; ask the team lead for the workaround |
| `git push` fails with "could not read Username" | Run the push from a terminal where you can complete the browser sign-in (an interactive VS Code terminal) |
| A change to `deployment/requirements.txt` is not picked up in Vocareum | Run `source scripts/setup_env.sh`; it reinstalls when the file changes |

---

## 7. Documents

| Document | Path |
|---|---|
| Requirements specification | [docs/AI_Capstone_Project_Requirements_v0.11.md](docs/AI_Capstone_Project_Requirements_v0.11.md) |
| Architecture specification | [docs/AI_Capstone_Project_Architecture_Specification_v1.5.md](docs/AI_Capstone_Project_Architecture_Specification_v1.5.md) |
| Architecture diagrams (Mermaid sources and PNG exports) | [docs/diagrams/](docs/diagrams/) |

**Document versions:** the specifications in `docs/` are the working copies. A change that alters a document increments its version, and the file is renamed to match (for example `..._v0.9.md` becomes `..._v0.10.md`), with an entry in the document's version history. Earlier versions stay available in git history.

**Dependencies:** pinned in `deployment/requirements.txt`, the only requirements file. Do not upgrade a library without re-running the dependency check in Req. Section 11.3 and the smoke test.
