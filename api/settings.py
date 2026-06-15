from pydantic_settings import BaseSettings, SettingsConfigDict

from api.constants import REDIS_URL


class Settings(BaseSettings):
    """Deploy/bootstrap configuration sourced from ``.env``.

    Only deployment-level settings that must exist before the app can start (or
    are docker-compose topology) live here. All app-domain configuration
    (embedding, vector store, parsers, search, mcp, file size) is owned by
    :class:`~api.models.config.AppConfig` and is editable via the config page.
    The single required value is ``MASTER_API_KEY``.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # The one required secret. Everything else has a deploy-sensible default.
    master_api_key: str

    # Infrastructure / bootstrap — needed before the app can serve requests.
    redis_url: str = REDIS_URL
    database_path: str = "/store/embedbase.db"
    upload_dir: str = "/data"

    # Middleware / process — applied at app startup.
    log_level: str = "info"
    log_format: str = "json"
    celery_concurrency: int = 2
    cors_origins: str = "http://localhost:3000"
    embedbase_secure_headers: bool = False

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",")]


settings = Settings()  # type: ignore[call-arg]
