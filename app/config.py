from dataclasses import dataclass
from pathlib import Path
import os

from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{(ROOT / 'study_abroad.db').as_posix()}")
    allowed_origins: tuple[str, ...] = tuple(
        origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "").split(",") if origin.strip()
    )
    seed_path: Path = ROOT / "data" / "programs.json"
    request_timeout_seconds: float = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "60"))
    ai_api_key: str = os.getenv("AI_API_KEY", "")
    ai_base_url: str = os.getenv("AI_BASE_URL", "https://api.deepseek.com")
    ai_model: str = os.getenv("AI_MODEL", "deepseek-flash")
    ai_timeout_seconds: float = float(os.getenv("AI_TIMEOUT_SECONDS", "25"))
    admin_token: str = os.getenv("ADMIN_TOKEN", "")
    vector_backend: str = os.getenv("VECTOR_BACKEND", "hash")
    vector_model_id: str = os.getenv(
        "VECTOR_MODEL_ID", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
    )
    vector_model_path: Path = ROOT / os.getenv("VECTOR_MODEL_PATH", ".models/multilingual-minilm")


settings = Settings()
