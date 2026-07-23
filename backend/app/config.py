from pathlib import Path

from pydantic_settings import BaseSettings

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    database_url: str = f"sqlite:///{BACKEND_ROOT / 'data' / 'dispatcher.db'}"
    seed_dir: Path = BACKEND_ROOT / "seed"
    data_dir: Path = BACKEND_ROOT / "data"
    frontend_dist_dir: Path = BACKEND_ROOT.parent / "frontend" / "dist"

    kanjivg_cache_dir: Path = BACKEND_ROOT / "data" / "kanjivg_cache"
    jisho_min_delay_seconds: float = 0.5

    model_config = {"env_prefix": "DISPATCHER_"}


settings = Settings()
settings.data_dir.mkdir(parents=True, exist_ok=True)
