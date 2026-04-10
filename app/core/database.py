from collections.abc import Generator

from sqlmodel import Session, create_engine

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    echo=False,
    pool_size=20,
    max_overflow=10,
    pool_recycle=1800,
)


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
