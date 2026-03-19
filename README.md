# FlyAgent Sandbox — Agents On The Fly

**A general-purpose agent sandbox where dynamically created AI agents can do anything — research, code, automate, and more.** Like OpenDevin/OpenClaw but built on the [AOrchestra ICTM framework](https://arxiv.org/abs/2602.03786), where every SubAgent is synthesized as a 4-tuple `φ = ⟨Instruction, Context, Tools, Model⟩` at runtime.

```
User Task (any kind: research, coding, automation, etc.)
    │
    ▼
┌──────────────────────────┐
│   Orchestrator             │  ← MainAgent (plans, delegates, decides)
│   Mode: general/research/  │
│   coding/automation        │
└──────────┬─────────────────┘
           │  Synthesizes ICTM tuples dynamically
           ▼
   ┌──────────────────────────────────────────┐
   │       delegate_task(I, C, T, M)           │
   │                                           │
   │  ┌─────────────┐  ┌───────────────────┐  │
   │  │ Shell Exec   │  │ Code Writer       │  │
   │  │ SubAgent     │  │ SubAgent          │  │
   │  │ T=[shell,    │  │ T=[file_edit,     │  │
   │  │   file_list] │  │   grep, python]   │  │
   │  └──────┬──────┘  └──────┬────────────┘  │
   │         │ report_back     │ report_back   │
   │  ┌──────┴────────────────┬┘               │
   │  │ Orchestrator evaluates results         │
   │  │ → delegates more or submits            │
   │  └────────────────────────────────────────│
   └───────────────────────────────────────────┘
           │
           ▼
    Final Result / Report
```

## Task Modes

| Mode | Description | Key Tools |
|------|-------------|-----------|
| **general** | Any task — auto-selects tools | All 13 tools |
| **research** | Find, analyze, synthesize info | web_search, arxiv, wikipedia, news, web_fetch |
| **coding** | Write, modify, debug code | shell_exec, file_edit, grep_search, python_exec |
| **automation** | Scripts, CI/CD, system tasks | shell_exec, python_exec, file_write |

## Key Principles (from AOrchestra)

- **MainAgent is the sole decision-maker** — it plans, delegates, and decides when the task is complete
- **SubAgents are pure executors** — they use tools and `report_back` raw results; they never decide completion
- **Dynamic creation** — no predefined roles; agents are purpose-built per subtask at runtime
- **Full sandbox access** — terminal, file system, web, code execution — all available to agents
- **Context curation** — only relevant prior results are passed to each SubAgent

---

## Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/flyagent.git
cd flyagent

# Setup
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env → add your Google API key: GOOGLE_API_KEY=your-key-here

# Run — general mode (any task)
python main.py "Build a Python script that scrapes HN headlines"

# Run — specific modes
python main.py --mode coding "Fix the bug in app.py"
python main.py --mode research "What are latest quantum computing breakthroughs?"
python main.py --mode automation "Set up a git pre-commit hook"

# Interactive mode
python main.py

# API + UI
python run_api.py &              # API at http://localhost:8000
python run_ui.py                 # UI  at http://localhost:3000
```

---

## Requirements

- **Python 3.10+**
- **Google AI API key** — get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- No other API keys needed — all tools work with free APIs

### Install Dependencies

```bash
pip install -r requirements.txt
```

---

## Usage

### CLI Mode

```bash
# General mode (default) — handles any task
python main.py "Create a web scraper for news headlines"

# Coding mode — optimized for code tasks
python main.py --mode coding "Refactor the database module to use async"

# Research mode — optimized for information gathering
python main.py --mode research "Compare transformer vs state space models"

# Automation mode — optimized for system tasks
python main.py --mode automation "Write a script to backup my database daily"
```

### API Mode

```bash
# Start a general task
curl -X POST http://localhost:8000/api/task \
  -H "Content-Type: application/json" \
  -d '{"query": "Build a REST API", "task_mode": "coding"}'

# Stream events
curl -N http://localhost:8000/api/task/{task_id}/events

# Legacy research endpoint still works
curl -X POST http://localhost:8000/api/research \
  -H "Content-Type: application/json" \
  -d '{"query": "What is ICTM?"}'
```

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/task` | Start any task (body: `{"query": "...", "task_mode": "..."}`) |
| `POST` | `/api/research` | Legacy research endpoint (auto-sets mode=research) |
| `GET` | `/api/task/{id}/events` | SSE stream of real-time events |
| `GET` | `/api/task/{id}` | Get task result |
| `GET` | `/api/config` | Get current configuration |
| `PUT` | `/api/config` | Update config at runtime |
| `GET` | `/api/health` | Health check |

---

## Tools (13 total)

### Sandbox / Execution Tools (NEW)

| Tool | Description |
|------|-------------|
| `shell_exec` | Execute any shell command (bash) — terminal access |
| `file_edit` | Find-and-replace editing within files |
| `file_list` | Directory tree exploration with file sizes |
| `grep_search` | Regex search across file contents |
| `python_exec` | Execute Python code in subprocess |
| `file_read` | Read files from workspace |
| `file_write` | Write/create files in workspace |

### Research / Web Tools

| Tool | Description |
|------|-------------|
| `web_search` | DuckDuckGo web search |
| `web_fetch` | Fetch URL, extract clean markdown |
| `arxiv_search` | Search academic papers |
| `wikipedia_search` | Wikipedia summaries |
| `news_search` | DuckDuckGo news search |
| `get_datetime` | Current UTC date/time |

---

## Configuration

### `config.toml` — Key Sections

| Section | What it controls |
|---------|-----------------|
| `[orchestrator]` | Task mode, model tier, max attempts, depth |
| `[sandbox]` | Sandbox level, permissions (shell, network, files) |
| `[models.*]` | Gemini model per tier, temperature, max tokens |
| `[tools.*]` | Enable/disable and tune each of the 13 tools |
| `[subagent]` | Max steps, timeout per SubAgent |
| `[output]` | Report/trajectory saving, verbosity |

### Sandbox Levels

```toml
[sandbox]
level = "standard"           # "strict" | "standard" | "permissive"
allow_network = true
allow_shell = true
allow_file_write = true
allow_package_install = true
working_dir = "./workspace"
```

### Environment Variables (`.env`)

```env
GOOGLE_API_KEY=your-google-api-key-here
# FLYAGENT_MAX_ATTEMPTS=12
# FLYAGENT_VERBOSE=true
```

---

## The ICTM Framework

Every SubAgent is defined by a frozen 4-tuple:

```
φ = ⟨I, C, T, M⟩
```

| Component | Description | Example |
|-----------|-------------|---------|
| **I** (Instruction) | Actionable task directive | "Create a REST API with FastAPI" |
| **C** (Context) | Curated info from prior subtasks | "Previous agent created the data models..." |
| **T** (Tools) | Subset of available tools | `["shell_exec", "file_write", "python_exec"]` |
| **M** (Model) | LLM tier selection | `"balanced"` → Gemini 2.5 Flash |

---

## Architecture

```
MainAgent (Orchestrator)
  │
  │── PLANS: Decomposes task into subtasks
  │── DELEGATES: Synthesizes ICTM tuple → spawns SubAgent
  │── EVALUATES: Reviews SubAgent results
  │── DECIDES: Sufficient? → submit_report. Need more? → delegate again.
  │
  └── SubAgent (Pure Executor)
        │── EXECUTES: Uses assigned tools (ReAct loop)
        │── Terminal access via shell_exec
        │── File operations via file_read/write/edit/list
        │── Code execution via python_exec
        │── Web access via web_search/fetch
        │── REPORTS BACK: Returns raw results to MainAgent
        └── NEVER decides completion — that's the MainAgent's job
```

---

## Acknowledgements

Inspired by:
- **[AOrchestra](https://arxiv.org/abs/2602.03786)** (Ruan et al.) — ICTM framework
- **[OpenDevin](https://github.com/OpenDevin/OpenDevin)** — agent sandbox concept

Powered by:
- [Google Gemini](https://ai.google.dev/) — LLM backbone
- [DuckDuckGo](https://duckduckgo.com/) — web & news search
- [ArXiv](https://arxiv.org/) — academic paper API

---

## License

MIT License. See [LICENSE](LICENSE) for details.
