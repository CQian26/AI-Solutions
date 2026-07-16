import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List
from dotenv import load_dotenv

BASE_URL = "https://mybidmatch.outreachsystems.com"
INDEX_PATH = "/go"
ARTICLE_PATH = "/article"


class ConfigError(RuntimeError):
    pass


@dataclass
class Config:
    token: str = field(repr=False)
    days: int = 7
    output: Path | None = None
    sam_api_key: str = field(default="", repr=False)
    cache_dir: Path = field(default_factory=lambda: Path(".cache"))

    def __str__(self) -> str:
        return (
            f"Config(days={self.days}, output={self.output}, "
            f"cache_dir={self.cache_dir}, token=<redacted>, sam_api_key=<redacted>)"
        )


def load_config() -> Config:
    if "PYTEST_CURRENT_TEST" not in os.environ:
        load_dotenv()
    token = os.environ.get("BIDMATCH_SUB", "").strip()
    if not token:
        raise ConfigError(
            "BIDMATCH_SUB is not set. Copy .env.example to .env and fill it in."
        )
    days_raw = os.environ.get("BIDMATCH_DAYS", "7").strip()
    try:
        days = int(days_raw)
    except ValueError as exc:
        raise ConfigError(
            f"BIDMATCH_DAYS must be a positive integer, got: {days_raw!r}"
        ) from exc
    if days <= 0:
        raise ConfigError(
            f"BIDMATCH_DAYS must be a positive integer, got: {days_raw!r}"
        )
    output_raw = os.environ.get("BIDMATCH_OUTPUT", "").strip()
    output = Path(output_raw) if output_raw else None
    sam_key = os.environ.get("SAM_API_KEY", "").strip()
    cache_raw = os.environ.get("CACHE_DIR", "").strip()
    cache_dir = Path(cache_raw) if cache_raw else Path(".cache")
    return Config(
        token=token,
        days=days,
        output=output,
        sam_api_key=sam_key,
        cache_dir=cache_dir,
    )


@dataclass
class AutorunConfig:
    smtp_host: str
    smtp_port: int
    smtp_user: str = field(repr=False, default="")
    smtp_password: str = field(repr=False, default="")
    notify_to: List[str] = field(default_factory=list)
    days: int = 2
    output_dir: Path = field(default_factory=lambda: Path("output"))
    cp_set_asides: List[str] = field(default_factory=lambda: ["small_business"])

    def __str__(self) -> str:
        return (
            f"AutorunConfig(smtp_host={self.smtp_host}, smtp_port={self.smtp_port}, "
            f"notify_to={self.notify_to}, days={self.days}, "
            f"output_dir={self.output_dir}, smtp_user=<redacted>, smtp_password=<redacted>)"
        )


def load_autorun_config() -> AutorunConfig:
    if "PYTEST_CURRENT_TEST" not in os.environ:
        load_dotenv()
    host = os.environ.get("SMTP_HOST", "").strip()
    recipients = [
        r.strip() for r in os.environ.get("NOTIFY_TO", "").split(",") if r.strip()
    ]
    if not host or not recipients:
        raise ConfigError("Autorun needs SMTP_HOST and NOTIFY_TO in .env")
    try:
        port = int(os.environ.get("SMTP_PORT", "587").strip())
        days = int(os.environ.get("AUTORUN_DAYS", "2").strip())
    except ValueError as exc:
        raise ConfigError("SMTP_PORT and AUTORUN_DAYS must be integers") from exc
    return AutorunConfig(
        smtp_host=host,
        smtp_port=port,
        smtp_user=os.environ.get("SMTP_USER", "").strip(),
        smtp_password=os.environ.get("SMTP_PASSWORD", "").strip(),
        notify_to=recipients,
        days=days,
        output_dir=Path(os.environ.get("AUTORUN_OUTPUT_DIR", "output").strip() or "output"),
        cp_set_asides=[
            s.strip() for s in os.environ.get("CP_SET_ASIDES", "small_business").split(",") if s.strip()
        ],
    )
