"""Engine/session management and programmatic Alembic migration."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


def _sqlite_pragmas(dbapi_conn: object, _: object) -> None:
    cursor = dbapi_conn.cursor()  # type: ignore[attr-defined]
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def make_engine(url: str) -> Engine:
    if url.startswith("sqlite"):
        if ":memory:" in url or url.rstrip("/") == "sqlite:":
            engine = create_engine(
                url, connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
        else:
            path = url.split("///", 1)[-1]
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})
        event.listen(engine, "connect", _sqlite_pragmas)
        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=10)


class Database:
    def __init__(self, url: str) -> None:
        self.url = url
        self.engine = make_engine(url)
        self._factory = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self._factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------ migrations
    def alembic_config(self) -> Config:
        script_location = str(
            resources.files("traceforge.infrastructure.database").joinpath("migrations")
        )
        cfg = Config()
        cfg.set_main_option("script_location", script_location)
        cfg.set_main_option("sqlalchemy.url", self.url.replace("%", "%%"))
        cfg.attributes["connection_engine"] = self.engine
        return cfg

    def migrate(self, revision: str = "head") -> None:
        command.upgrade(self.alembic_config(), revision)

    def current_revision(self) -> str | None:
        with self.engine.connect() as conn:
            return MigrationContext.configure(conn).get_current_revision()

    def head_revision(self) -> str | None:
        return ScriptDirectory.from_config(self.alembic_config()).get_current_head()

    def is_up_to_date(self) -> bool:
        return self.current_revision() == self.head_revision()

    def dispose(self) -> None:
        self.engine.dispose()
