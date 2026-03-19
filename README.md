# FlyAgent — Dynamic ICTM-Based Research Agent

**Create specialized AI SubAgents on the fly.** Inspired by the [AOrchestra paper](https://arxiv.org/abs/2602.03786) ([code](https://github.com/FoundationAgents/AOrchestra)), FlyAgent implements the ICTM framework where every SubAgent is dynamically synthesized as a 4-tuple `φ = ⟨Instruction, Context, Tools, Model⟩` — no predefined roles, no static agent definitions.

```
User Query
    │
    ▼
┌──────────────────────┐
│   Orchestrator        │  ← MainAgent (plans, delegates, decides completion)
│   (Gemini 2.5 Flash)  │
└──────────┬───────────┘
           │  Synthesizes ICTM tuples dynamically
           ▼
   ┌─────────────────────────────────────┐
   │       delegate_task(I, C, T, M)      │
   │                                      │
   │  ┌────────────┐  ┌───────────────┐  │
   │  │ WebSearch   │  │ ArXiv Search  │  │
   │  │ SubAgent    │  │ SubAgent      │  │
   │  │ M=fast      │  │ M=fast        │  │
   │  │ T=[search]  │  │ T=[arxiv]     │  │
   │  └──────┬─────┘  └──────┬────────┘  │
   │         │ report_back    │ report_back│
   │  ┌──────┴──────────────┬─┘           │
   │  │ Orchestrator evaluates findings   │
   │  │ → delegates more or submits       │
   │  └──────────────────────────────────┘│
   └─────────────────────────────────────┘
           │
           ▼
    Final Research Report
```

## Key Principles (from AOrchestra)

- **MainAgent is the sole decision-maker** — it plans, delegates, and decides when research is complete
- **SubAgents are pure executors** — they use tools and `report_back` raw findings; they never decide completion
- **Dynamic creation** — no predefined roles; agents are purpose-built per subtask at runtime
- **Context curation** — only relevant prior findings are passed to each SubAgent
- **Model tier selection** — cheap models for searches, expensive for synthesis

---

## Quick Start

```bash
# Clone
git clone https://github.com/YOUR_USERNAME/flyagent.git
cd flyagent

# Setup
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt

# Configure
cp .env.example .env
# Edit .env → add your Google API key: GOOGLE_API_KEY=your-key-here

# Run (CLI)
python main.py "What are the latest breakthroughs in quantum computing?"

# Or run with API + UI
python run_api.py &              # API at http://localhost:8000
python run_ui.py                 # UI  at http://localhost:3000
```

---

## Requirements

- **Python 3.10+**
- **Google AI API key** — get one at [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
- No other API keys needed — all 9 tools work with free APIs (DuckDuckGo, ArXiv, Wikipedia)

### Install Dependencies

```bash
pip install -r requirements.txt
```

Or individually:
```bash
pip install google-generativeai pydantic httpx html2text ddgs python-dotenv rich tomli fastapi uvicorn
```

---

## Usage

### CLI Mode

```bash
python main.py "Explain the current state of nuclear fusion energy research"
python main.py "Compare transformer architectures: Mamba vs traditional attention"
```

Interactive mode (no arguments):
```bash
python main.py
# Prompts: Enter your research query: _
```

### API + UI Mode

Start the API server and UI separately (they are independently deployable):

```bash
# Terminal 1 — API server (FastAPI + Uvicorn)
python run_api.py
# → http://localhost:8000

# Terminal 2 — UI server (static files)
python run_ui.py
# → http://localhost:3000
```

Open http://localhost:3000 in your browser to see:
- **Live agent pipeline** — orchestrator + subagent cards with ICTM parameters, real-time step progress bars
- **Streaming logs** — filterable by level (INFO/WARN/ERROR), auto-scrolling
- **Dark/Light mode** — toggle via the sun/moon icon in the top-right header (persists across sessions)
- **Runtime config** — change models, budgets, and tools via the settings modal (applied in-memory)

### API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/research` | Start research (body: `{"query": "..."}`) → returns `research_id` |
| `GET` | `/api/research/{id}/events` | SSE stream of real-time events |
| `GET` | `/api/research/{id}` | Get research result |
| `GET` | `/api/config` | Get current configuration |
| `PUT` | `/api/config` | Update config at runtime (in-memory) |
| `GET` | `/api/health` | Health check |

**curl example:**
```bash
# Start research
curl -X POST http://localhost:8000/api/research \
  -H "Content-Type: application/json" \
  -H "Origin: http://localhost:3000" \
  -d '{"query": "What is the ICTM framework?"}'

# Stream events
curl -N http://localhost:8000/api/research/{research_id}/events
```

---

## The ICTM Framework

Every SubAgent is defined by a frozen 4-tuple:

```
φ = ⟨I, C, T, M⟩
```

| Component | Description | Example |
|-----------|-------------|---------|
| **I** (Instruction) | Actionable task directive | "Search ArXiv for quantum error correction papers from 2025" |
| **C** (Context) | Curated info from prior subtasks | "Previous search found these 3 key papers: ..." |
| **T** (Tools) | Subset of available tools | `["arxiv_search", "web_fetch"]` |
| **M** (Model) | LLM tier selection | `"fast"` → Gemini 2.0 Flash |

```python
@dataclass(frozen=True)
class ICTM:
    instruction: str
    context: str = ""
    tools: list[str] = field(default_factory=list)
    model_tier: str = "balanced"
```

---

## Architecture

### AOrchestra-Aligned Design

```
MainAgent (Orchestrator)
  │
  │── PLANS: Decomposes query into subtasks
  │── DELEGATES: Synthesizes ICTM tuple → spawns SubAgent
  │── EVALUATES: Reviews SubAgent findings
  │── DECIDES: Sufficient? → submit_report. Need more? → delegate again.
  │
  └── SubAgent (Pure Executor)
        │── EXECUTES: Uses assigned tools (ReAct loop)
        │── REPORTS BACK: Returns raw findings to MainAgent
        └── NEVER decides completion — that's the MainAgent's job
```

### Data Flow

```
Orchestrator ──→ LLM ──→ "delegate_task" ──→ ICTM(I, C, T, M)
     │                                              │
     │                                        SubAgent #N
     │                                     ┌────────┴────────┐
     │                                     │  ReAct Loop      │
     │                                     │  tool → observe  │
     │                                     │  tool → observe  │
     │                                     │  report_back     │
     │                                     └────────┬────────┘
     │                                              │
     │◄─────────── raw findings ────────────────────┘
     │
Orchestrator ──→ LLM ──→ evaluates findings
     │                         │
     ├── need more? ──→ delegate_task (next subtask)
     └── sufficient? ──→ submit_report
```

### System Components

```
┌─────────────────────────────────────────────────────────────┐
│                         API Layer                            │
│  api/app.py          FastAPI endpoints + SSE streaming       │
│  api/events.py       EventBus pub/sub for real-time events   │
└──────────────────────────┬──────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│                       Core Engine                            │
│  flyagent/orchestrator.py   MainAgent — plan/delegate/eval   │
│  flyagent/subagent.py       SubAgent — ReAct tool executor   │
│  flyagent/ictm.py           ICTM 4-tuple dataclass           │
│  flyagent/llm.py            Google Gemini LLM wrapper        │
│  flyagent/config.py         TOML + .env config loader        │
│  flyagent/logging_setup.py  OTEL-compliant structured logs   │
│  flyagent/prompts/          Prompt templates                 │
│  flyagent/tools/            9 tool implementations           │
└─────────────────────────────────────────────────────────────┘
                           │
┌──────────────────────────┴──────────────────────────────────┐
│                         UI Layer                             │
│  ui/index.html       Single-file UI (Alpine.js + Tailwind)   │
│  run_ui.py           Static file server (port 3000)          │
└─────────────────────────────────────────────────────────────┘
```

---

## Configuration

All configuration lives in **`config.toml`**. Secrets go in **`.env`**.

### Environment Variables (`.env`)

```env
# Required
GOOGLE_API_KEY=your-google-api-key-here

# Optional overrides
# FLYAGENT_FAST_MODEL=gemini-2.0-flash
# FLYAGENT_BALANCED_MODEL=gemini-2.5-flash
# FLYAGENT_POWERFUL_MODEL=gemini-2.5-pro
# FLYAGENT_MAX_ATTEMPTS=12
# FLYAGENT_VERBOSE=true
```

> You can also use `GEMINI_API_KEY` — FlyAgent checks both.

### Key Config Sections (`config.toml`)

| Section | What it controls |
|---------|-----------------|
| `[orchestrator]` | Model tier, max attempts, concurrency |
| `[models.fast/balanced/powerful]` | Gemini model per tier, temperature, max tokens |
| `[tools.*]` | Enable/disable and tune each of the 9 tools |
| `[subagent]` | Max steps, timeout per SubAgent |
| `[output]` | Report/trajectory saving, verbosity |
| `[server]` | API host/port, CORS, UI port |
| `[logging]` | Level, format (json/text), file/console output |

See [`config.toml`](config.toml) for the full reference with comments.

---

## Tools

9 built-in tools — **no extra API keys required** (only `GOOGLE_API_KEY` for the LLM):

| Tool | Source | Description |
|------|--------|-------------|
| `web_search` | DuckDuckGo | General web search |
| `web_fetch` | httpx + html2text | Fetch URL, extract clean text |
| `arxiv_search` | ArXiv API | Search academic papers |
| `wikipedia_search` | Wikipedia REST API | Get encyclopedic summaries |
| `news_search` | DuckDuckGo News | Recent news articles |
| `file_read` | Local filesystem | Read files from workspace (sandboxed) |
| `file_write` | Local filesystem | Write files to workspace (sandboxed) |
| `python_exec` | subprocess | Execute Python code |
| `get_datetime` | Python stdlib | Current UTC date/time |

Each tool can be toggled and tuned in `config.toml`:

```toml
[tools.web_search]
enabled = true
max_results = 8

[tools.python_exec]
enabled = false    # disable if you don't want code execution
```

---

## Logging

OTEL-compliant structured logging, configurable in `config.toml`:

```toml
[logging]
level = "INFO"              # DEBUG | INFO | WARNING | ERROR
format = "json"             # json (OTEL) | text (human-readable)
log_to_console = true
log_to_file = true
log_file = "./workspace/logs/flyagent.log"
service_name = "flyagent"
```

JSON log output follows the OTEL log data model:
```json
{
  "Timestamp": "2026-03-18T10:30:00Z",
  "SeverityText": "INFO",
  "SeverityNumber": 9,
  "Body": "Spawning SubAgent #1",
  "Resource": {"service.name": "flyagent"},
  "Attributes": {"research_id": "abc123", "agent_type": "orchestrator"},
  "TraceId": "..."
}
```

---

## Project Structure

```
flyagent/
├── .env.example               # Environment template
├── config.toml                # All configuration
├── requirements.txt           # Python dependencies
├── pyproject.toml             # Project metadata
├── main.py                    # CLI entry point
├── run_api.py                 # API server (uvicorn)
├── run_ui.py                  # UI server (static files)
│
├── api/                       # API layer (independently deployable)
│   ├── __init__.py
│   ├── app.py                 # FastAPI routes + SSE streaming
│   └── events.py              # EventBus pub/sub
│
├── flyagent/                  # Core engine
│   ├── __init__.py
│   ├── config.py              # TOML + .env config loader (Pydantic)
│   ├── ictm.py                # ICTM 4-tuple dataclass
│   ├── llm.py                 # Google Gemini wrapper
│   ├── logging_setup.py       # OTEL-compliant logging
│   ├── orchestrator.py        # MainAgent — plan/delegate/evaluate/complete
│   ├── subagent.py            # SubAgent — ReAct executor (report_back)
│   ├── prompts/
│   │   ├── orchestrator.py    # Orchestrator system & step prompts
│   │   └── subagent.py        # SubAgent system & step prompts
│   └── tools/
│       ├── __init__.py        # Tool registry (factory pattern)
│       ├── web_search.py      # DuckDuckGo web search
│       ├── web_fetch.py       # URL content extraction
│       ├── arxiv_tool.py      # ArXiv paper search
│       ├── wikipedia_tool.py  # Wikipedia search
│       ├── news_search.py     # DuckDuckGo news
│       ├── file_ops.py        # Sandboxed file read/write
│       ├── python_exec.py     # Python code execution
│       └── datetime_tool.py   # Current date/time
│
├── ui/                        # UI layer (independently deployable)
│   └── index.html             # Single-file UI (Alpine.js + Tailwind CSS)
│
└── workspace/                 # Runtime output (gitignored)
    ├── *.md                   # Generated reports
    ├── trajectories/*.json    # Execution traces
    └── logs/flyagent.log      # Structured logs
```

---

## Extending FlyAgent

### Adding a New Tool

1. Create `flyagent/tools/my_tool.py`:

```python
from flyagent.tools import ToolInfo

def create_tool(cfg):
    async def execute(query: str) -> str:
        return f"Result for: {query}"

    return ToolInfo(
        name="my_tool",
        description="What this tool does.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Input"},
            },
            "required": ["query"],
        },
        execute=execute,
    )
```

2. Register in `flyagent/tools/__init__.py`:

```python
@_register("my_tool")
def _make_my_tool(cfg):
    from flyagent.tools.my_tool import create_tool
    return create_tool(cfg)
```

3. Add config:

```toml
[tools.my_tool]
enabled = true
```

### Adding a New LLM Provider

Extend `flyagent/llm.py` — the `ModelConfig.provider` field already exists. Add a new branch in `create_model()` for OpenAI, Anthropic, etc.

---

## Acknowledgements

This project is inspired by:

- **[AOrchestra](https://arxiv.org/abs/2602.03786)** (Ruan et al.) — the ICTM framework and dynamic SubAgent orchestration concept
- **[AOrchestra Code](https://github.com/FoundationAgents/AOrchestra)** — reference implementation

Powered by:
- [Google Gemini](https://ai.google.dev/) — LLM backbone
- [DuckDuckGo](https://duckduckgo.com/) — free web & news search
- [ArXiv](https://arxiv.org/) — academic paper API

---

## License

MIT License. See [LICENSE](LICENSE) for details.
