# app/config.py
from dataclasses import dataclass
from pathlib import Path
import os
from dotenv import load_dotenv

def _str2bool(x: str | None, default: bool = True) -> bool:
    if x is None:
        return default
    x = x.strip().lower()
    return x in {"1", "true", "t", "yes", "y", "on"}

@dataclass(frozen=True)
class Settings:
    DISCORD_BOT_TOKEN: str
    OWNER_ID: int
    GUILD_ID: int
    RESTART_MODE: str
    DB_PATH: Path
    RIOT_API_KEY: str | None
    ENABLE_TRASH_TALK: bool  # nouveau flag

def load_settings() -> Settings:
    # Charge .env à côté de ce fichier (si présent)
    load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_BOT_TOKEN manquant")

    return Settings(
        DISCORD_BOT_TOKEN=token,
        OWNER_ID=int(os.getenv("OWNER_ID", "0")),
        GUILD_ID=int(os.getenv("GUILD_ID", "0")),
        RESTART_MODE=os.getenv("RESTART_MODE", "manager"),
        DB_PATH=Path(os.getenv("DB_PATH", str(Path(__file__).parents[1] / "skills.db"))),
        RIOT_API_KEY=os.getenv("RIOT_API_KEY") or None,
        ENABLE_TRASH_TALK=_str2bool(os.getenv("ENABLE_TRASH_TALK"), default=True),
    )
