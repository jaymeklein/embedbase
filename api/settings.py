from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Infrastructure
    redis_url: str = "redis://redis:6379/0"
    database_path: str = "/store/embedbase.db"
    master_api_key: str

    # Vector store
    vector_store: str = "chroma"
    chroma_host: str = "chroma"
    chroma_port: int = 8001
    chroma_auth_token: str = "embedbase-internal"
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    postgres_db: str = "embedbase"
    postgres_user: str = "embedbase"
    postgres_password: str = ""
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333

    # Embedding
    embedding_provider: str = "sentence_transformers"
    embedding_model: str = "all-MiniLM-L6-v2"
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_concurrency: int = 8
    openai_compat_base_url: str = "http://host.docker.internal:1234"
    openai_compat_api_key: str = ""

    # Application
    log_level: str = "info"
    log_format: str = "json"
    max_file_size_mb: int = 50
    celery_concurrency: int = 2
    cors_origins: str = "http://localhost:3000"
    embedbase_secure_headers: bool = False

    @property
    def max_file_size_bytes(self) -> int:
        return self.max_file_size_mb * 1024 * 1024

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


settings = Settings()  # type: ignore[call-arg]
