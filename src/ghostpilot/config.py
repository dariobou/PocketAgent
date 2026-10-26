"""GhostPilot configuration management via Pydantic Settings."""


from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ghostpilot.models.enums import AgentType


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Telegram Configuration
    telegram_bot_token: str = Field(default="", description="Telegram Bot Token from @BotFather")
    telegram_allowed_user_ids: list[int] = Field(
        default_factory=list,
        description="List of authorized Telegram user IDs",
    )
    telegram_chat_id: int | None = Field(
        default=None,
        description="Active chat ID for alerts & streaming",
    )
    telegram_webhook_secret: str = Field(
        default="",
        description="Secret token passed in X-Telegram-Bot-Api-Secret-Token",
    )
    cloudflare_worker_url: str = Field(
        default="",
        description="Public URL of the edge Cloudflare Worker for webhook restoration",
    )

    # Runtime & Local Control Plane Settings
    host: str = Field(default="127.0.0.1", description="FastAPI bind host")
    port: int = Field(default=8765, description="FastAPI bind port")
    idle_timeout_seconds: int = Field(default=300, description="Shutdown after idle inactivity")
    max_session_minutes: int = Field(default=25, description="Max lifetime budget before teardown")
    auto_approve_readonly: bool = Field(
        default=True,
        description="Auto approve read-only tools without Telegram prompt",
    )
    hitl_timeout_seconds: float = Field(
        default=290.0,
        description="Timeout waiting for user approval on Telegram",
    )

    # Workspace & Repo Settings
    workspace_dir: str = Field(
        default="/tmp/workspace",
        description="Base directory for cloned repositories",
    )
    target_repo: str | None = Field(
        default=None,
        description="GitHub repo in 'owner/repo' format",
    )
    default_branch: str = Field(default="main", description="Default branch to target")
    active_agent: AgentType = Field(default=AgentType.CLAUDE, description="Default active agent")
    initial_prompt: str | None = Field(default=None, description="Initial prompt passed to agent")

    # GitHub Credentials
    github_pat: str = Field(
        default="",
        description="GitHub Personal Access Token for git push & PR creation",
    )
    gh_owner: str | None = Field(default=None, description="GitHub repository owner")
    gh_repo: str | None = Field(default=None, description="GitHub repository name")

    # BYOS Credentials
    claude_code_oauth_token: str | None = Field(
        default=None,
        description="OAuth token from claude setup-token",
    )
    anthropic_api_key: str | None = Field(default=None, description="Anthropic API Key fallback")
    codex_auth_json_b64: str | None = Field(
        default=None,
        description="Base64 encoded ~/.codex/auth.json",
    )
    openai_api_key: str | None = Field(default=None, description="OpenAI API Key fallback")
    gemini_oauth_b64: str | None = Field(
        default=None,
        description="Base64 encoded ~/.gemini/oauth_creds.json",
    )
    gemini_api_key: str | None = Field(default=None, description="Gemini API Key fallback")

    @field_validator("telegram_allowed_user_ids", mode="before")
    @classmethod
    def parse_allowed_ids(cls, v: object) -> list[int]:
        if isinstance(v, str):
            if not v.strip():
                return []
            return [int(x.strip()) for x in v.split(",") if x.strip().isdigit()]
        if isinstance(v, int):
            return [v]
        if isinstance(v, list):
            return [int(x) for x in v]
        return []


settings = Settings()
