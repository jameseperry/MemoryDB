from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    async_database_url: str = "postgresql://memory:memory@localhost:5432/memory"
    database_url: str = "postgresql+psycopg2://memory:memory@localhost:5432/memory"
    ollama_url: str = "http://localhost:11434"
    ollama_embed_model: str = "nomic-embed-text"
    mcp_port: int = 8765
    mcp_workspace_header: str = "X-Memory-Workspace"

    # asyncpg pool sizing
    db_min_connections: int = 2
    db_max_connections: int = 10


settings = Settings()
