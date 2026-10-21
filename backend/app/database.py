from sqlmodel import SQLModel, create_engine, Session
from sqlalchemy import text
from .config import settings

engine = create_engine(settings.database_url)


def get_session():
    with Session(engine) as session:
        yield session


def init_db():
    # Additive table creation is serialized across simultaneous API/worker
    # starts. Operators still perform explicit reviewed column migrations.
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            connection.execute(text("SELECT pg_advisory_xact_lock(62859123741011)"))
        elif connection.dialect.name == "sqlite":
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        SQLModel.metadata.create_all(connection)
