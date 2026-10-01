"""Database configuration and session management."""

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from sqlalchemy.engine import Engine
from sqlalchemy.pool import QueuePool, NullPool
import os
import time

# Use SQLite for simplicity - can be swapped for PostgreSQL later
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./debate.db")

# Connection pool settings
POOL_SIZE = int(os.getenv("DB_POOL_SIZE", "5"))
POOL_MAX_OVERFLOW = int(os.getenv("DB_POOL_MAX_OVERFLOW", "10"))
POOL_RECYCLE = int(os.getenv("DB_POOL_RECYCLE", "900"))  # 15 minutes
POOL_PRE_PING = os.getenv("DB_POOL_PRE_PING", "true").lower() == "true"

# Determine pool class based on database type
if DATABASE_URL.startswith("sqlite"):
    pool_class = NullPool  # SQLite doesn't handle pooling well
else:
    pool_class = QueuePool

common_kwargs = {
    "echo": False,
    "pool_recycle": POOL_RECYCLE,
    "pool_pre_ping": POOL_PRE_PING,
}

if pool_class == NullPool:
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False},
        **common_kwargs,
    )
else:
    engine = create_engine(
        DATABASE_URL,
        poolclass=pool_class,
        pool_size=POOL_SIZE,
        max_overflow=POOL_MAX_OVERFLOW,
        **common_kwargs,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    """Enable foreign key support for SQLite."""
    if DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db():
    """FastAPI dependency: yield a database session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """Get a new database session."""
    return SessionLocal()


def init_db():
    """Initialize database tables and run auto-migrations."""
    Base.metadata.create_all(bind=engine)
    _run_auto_migrations()


def _run_auto_migrations():
    """Apply schema changes that SQLAlchemy create_all can't handle (new columns on existing tables)."""
    if not DATABASE_URL.startswith("sqlite"):
        return
    import sqlite3
    # Extract the file path from sqlite:///... URL (handles relative + absolute paths).
    db_path = DATABASE_URL.replace("sqlite:///", "", 1)
    if not db_path or db_path == ":memory:":
        return
    # (table, column, type) tuples to add if missing. create_all handles new
    # TABLES, but not new COLUMNS on existing tables.
    migrations = [
        ("participants", "session_token_hash", "VARCHAR(64)"),
    ]
    try:
        conn = sqlite3.connect(db_path)
        for table, col_name, col_type in migrations:
            try:
                cursor = conn.execute(f"PRAGMA table_info({table})")
                cols = {row[1] for row in cursor.fetchall()}
                if col_name not in cols:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}")
                    conn.commit()
            except Exception:
                pass
        conn.close()
    except Exception:
        pass  # Migration failure is non-fatal


def check_db_health() -> dict:
    """Check database connectivity and return health status."""
    start = time.time()
    try:
        with engine.connect() as conn:
            if DATABASE_URL.startswith("sqlite"):
                conn.execute(text("SELECT 1"))
            else:
                conn.execute(text("SELECT 1"))
            latency_ms = (time.time() - start) * 1000
            return {
                "status": "healthy",
                "latency_ms": round(latency_ms, 2),
                "pool_size": POOL_SIZE,
                "pool_max_overflow": POOL_MAX_OVERFLOW,
            }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e),
            "latency_ms": round((time.time() - start) * 1000, 2),
        }
