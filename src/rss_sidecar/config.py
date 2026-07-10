from pydantic_settings import BaseSettings, SettingsConfigDict
from pathlib import Path


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4o-mini"

    translation_input_price: float = 0.0
    translation_output_price: float = 0.0

    freshrss_url: str = ""
    freshrss_username: str = ""
    freshrss_api_password: str = ""

    daily_budget_usd: float = 1.0
    max_articles_per_day: int = 100
    max_retries: int = 2

    host: str = "0.0.0.0"
    port: int = 8000

    db_path: str = "data/rss_sidecar.db"
    target_language: str = "zh-CN"

    fetch_interval_seconds: int = 1800

    @property
    def freshrss_enabled(self) -> bool:
        return bool(self.freshrss_url and self.freshrss_username and self.freshrss_api_password)


settings = Settings()

DATA_DIR = Path(settings.db_path).parent
DATA_DIR.mkdir(parents=True, exist_ok=True)
