from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from src.backend.core.config import settings
from collections.abc import AsyncGenerator

# Create the async engine connected to PostgreSQL
engine = create_async_engine(settings.DATABASE_URL, echo=False)

# Create a session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that provides an asynchronous database session.
    Using 'yield' ensures the session is properly closed after the request finishes.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()