from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.backend.core.config import settings

# Create the async engine connected to PostgreSQL
engine = create_async_engine(settings.DATABASE_URL, echo=False)

# Create a session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    autocommit=False,
    autoflush=False,
    expire_on_commit=False,
)