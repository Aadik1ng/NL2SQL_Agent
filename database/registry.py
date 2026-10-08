"""The database registry, config/databases.toml. It's re-read on every call, so edits apply to the next question."""
import os
import tomllib
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = Path(os.environ.get("DATABASES", ROOT / "config" / "databases.toml"))


def load_registry():
    """The configured databases, context and as-of date, read fresh from databases.toml."""
    return tomllib.loads(REGISTRY.read_text())


def db_path(database):
    databases = load_registry()["databases"]
    if database not in databases:
        raise ValueError(f"unknown database {database!r}; configured: {', '.join(databases)}")
    return (REGISTRY.parent / databases[database]["path"]).resolve()


def as_of():
    return load_registry().get("as_of") or date.today().isoformat()
