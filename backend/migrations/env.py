"""CPA-Agent MySQL migrations. Never run from an HTTP request or app startup."""

from alembic import context
import re
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.config import Config


def run_migrations_online() -> None:
    database = context.get_x_argument(as_dictionary=True).get("database", Config.MYSQL_DATABASE)
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", database):
        raise RuntimeError("Invalid migration database name")
    url = URL.create(
        "mysql+pymysql",
        username=Config.MYSQL_USER,
        password=Config.MYSQL_PASSWORD,
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        database=database,
        query={"charset": "utf8mb4"},
    )
    engine = create_engine(url, poolclass=NullPool)
    try:
        with engine.connect() as connection:
            if connection.dialect.name != "mysql":
                raise RuntimeError("CPA-Agent migrations require MySQL")
            context.configure(connection=connection, target_metadata=None)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline SQL generation is not supported for conditional MySQL migrations")
run_migrations_online()
