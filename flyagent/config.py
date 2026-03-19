"""Configuration loader: TOML config + .env overrides."""

from __future__ import annotations

import os
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib  # pip install tomli for Python <3.11
from typing import Any

from dotenv import load_dotenv
from pydantic import BaseModel, Field


PROJECT_ROOT = Path(__file__).parent.parent


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    if p.is_absolute():
        return p
    return (PROJECT_ROOT / p).resolve()


class ModelConfig(BaseModel):
    provider: str = "google"
    model: str
    temperature: float = 0.4
    max_output_tokens: int = 8192


class ToolConfig(BaseModel):
    enabled: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class SubagentConfig(BaseModel):
    max_steps: int = 20
    timeout_seconds: int = 300


class OutputConfig(BaseModel):
    format: str = "markdown"
    save_trajectory: bool = True
    trajectory_dir: Path = Field(default_factory=lambda: _resolve("./workspace/trajectories"))
    save_final_report: bool = True
    report_dir: Path = Field(default_factory=lambda: _resolve("./workspace"))
    verbose: bool = True


class OrchestratorConfig(BaseModel):
    model_tier: str = "balanced"
    max_attempts: int = 12
    max_concurrent_subagents: int = 3
    min_subtasks: int = 3              # Minimum subtasks before submit_report is allowed
    research_depth: str = "thorough"   # "quick" | "moderate" | "thorough"


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8000
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    ui_port: int = 3000


class LoggingConfig(BaseModel):
    level: str = "INFO"
    format: str = "json"
    log_to_console: bool = True
    log_to_file: bool = True
    log_file: str = "./workspace/logs/flyagent.log"
    service_name: str = "flyagent"


class AppConfig(BaseModel):
    project_name: str = "deep-research-agent"
    workspace_dir: Path = Field(default_factory=lambda: _resolve("./workspace"))
    orchestrator: OrchestratorConfig = Field(default_factory=OrchestratorConfig)
    models: dict[str, ModelConfig] = Field(default_factory=dict)
    tools: dict[str, ToolConfig] = Field(default_factory=dict)
    subagent: SubagentConfig = Field(default_factory=SubagentConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def get_model(self, tier: str) -> ModelConfig:
        if tier not in self.models:
            raise ValueError(f"Unknown model tier '{tier}'. Available: {list(self.models.keys())}")
        return self.models[tier]

    def to_ui_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dict for the config UI panel."""
        return {
            "orchestrator": self.orchestrator.model_dump(),
            "models": {k: v.model_dump() for k, v in self.models.items()},
            "subagent": self.subagent.model_dump(),
            "tools": {k: {"enabled": v.enabled, **v.extra} for k, v in self.tools.items()},
            "output": {
                "verbose": self.output.verbose,
                "save_trajectory": self.output.save_trajectory,
                "save_final_report": self.output.save_final_report,
            },
            "logging": self.logging.model_dump(),
        }


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load config from TOML file with .env overrides."""
    load_dotenv(PROJECT_ROOT / ".env")

    if config_path is None:
        config_path = PROJECT_ROOT / "config.toml"
    config_path = Path(config_path)

    with config_path.open("rb") as f:
        raw = tomllib.load(f)

    # Build model configs
    models: dict[str, ModelConfig] = {}
    for tier, mdata in raw.get("models", {}).items():
        env_key = f"FLYAGENT_{tier.upper()}_MODEL"
        if os.getenv(env_key):
            mdata["model"] = os.getenv(env_key)
        models[tier] = ModelConfig(**mdata)

    # Build tool configs
    tools: dict[str, ToolConfig] = {}
    for tname, tdata in raw.get("tools", {}).items():
        enabled = tdata.pop("enabled", True)
        tools[tname] = ToolConfig(enabled=enabled, extra=tdata)

    # Orchestrator
    orch_raw = raw.get("orchestrator", {})
    if os.getenv("FLYAGENT_MAX_ATTEMPTS"):
        orch_raw["max_attempts"] = int(os.getenv("FLYAGENT_MAX_ATTEMPTS"))
    if os.getenv("FLYAGENT_MAX_CONCURRENT"):
        orch_raw["max_concurrent_subagents"] = int(os.getenv("FLYAGENT_MAX_CONCURRENT"))

    # Subagent
    sub_raw = raw.get("subagent", {})

    # Output
    out_raw = raw.get("output", {})
    if os.getenv("FLYAGENT_VERBOSE"):
        out_raw["verbose"] = os.getenv("FLYAGENT_VERBOSE").lower() in ("true", "1", "yes")
    if "trajectory_dir" in out_raw:
        out_raw["trajectory_dir"] = _resolve(out_raw["trajectory_dir"])
    if "report_dir" in out_raw:
        out_raw["report_dir"] = _resolve(out_raw["report_dir"])

    # Server
    srv_raw = raw.get("server", {})

    # Logging
    log_raw = raw.get("logging", {})

    # Project
    proj = raw.get("project", {})

    return AppConfig(
        project_name=proj.get("name", "deep-research-agent"),
        workspace_dir=_resolve(proj.get("workspace_dir", "./workspace")),
        orchestrator=OrchestratorConfig(**orch_raw),
        models=models,
        tools=tools,
        subagent=SubagentConfig(**sub_raw),
        output=OutputConfig(**out_raw),
        server=ServerConfig(**srv_raw),
        logging=LoggingConfig(**log_raw),
    )
