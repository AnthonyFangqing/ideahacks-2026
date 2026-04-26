from dataclasses import dataclass
from pathlib import Path
import os


PACKAGE_DIR = Path(__file__).resolve().parent
SERVER_DIR = PACKAGE_DIR.parent


@dataclass(frozen=True)
class ServerConfig:
    library_path: Path
    device_poll_seconds: float
    cors_origin: str


def _path_from_env(name: str, default: Path) -> Path:
    value = os.environ.get(name)
    return Path(value).expanduser().resolve() if value else default


def load_config() -> ServerConfig:
    return ServerConfig(
        library_path=_path_from_env(
            "IDEAHACKS_LIBRARY_PATH",
            SERVER_DIR / "library",
        ),
        device_poll_seconds=float(
            os.environ.get("IDEAHACKS_DEVICE_POLL_SECONDS", "1.0")
        ),
        cors_origin=os.environ.get("IDEAHACKS_CORS_ORIGIN", "*"),
    )
