"""Settings for the v3 Memory MCP scaffold."""

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
    )

    async_database_url: str = Field(
        default="postgresql://memory:memory@localhost:5432/memory_v3",
        validation_alias=AliasChoices(
            "ASYNC_DATABASE_URL_V3",
            "MEMORY_V3_ASYNC_DATABASE_URL",
        ),
    )
    database_url: str = Field(
        default="postgresql+psycopg2://memory:memory@localhost:5432/memory_v3",
        validation_alias=AliasChoices(
            "DATABASE_URL_V3",
            "MEMORY_V3_DATABASE_URL",
        ),
    )
    mcp_workspace_header: str = "X-Memory-Workspace"
    mcp_session_header: str = "X-Memory-Session-Id"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8765
    embed_model_name: str = "nomic-ai/nomic-embed-text-v1.5"

    db_min_connections: int = 2
    db_max_connections: int = 10

    # Retrieval and consolidation tuning.
    bring_to_mind_idle_reset_minutes: int = 30
    bring_to_mind_search_limit: int = 12
    bring_to_mind_result_limit: int = 6
    recall_search_limit: int = 5
    query_observations_search_limit: int = 100
    search_recent_observation_window_days: int = 7
    search_recent_observation_bonus: float = 0.05
    search_understanding_score_multiplier: float = 1.15
    dense_intersection_min_size: int = 2


settings = Settings()
