import asyncio
import asyncpg
from sqlalchemy.engine import make_url
from src.backend.core.config import settings

async def create_database_if_not_exists():
    # Parse the database name from your settings
    url = make_url(settings.DATABASE_URL)
    db_name = url.database

    # We must connect to the defalt 'postgres' database to issue a CREATE DATABASE command
    sys_url = settings.DATABASE_URL.replace(f"/{db_name}", "/postgres")
    # asyncpg expects 'postgresql://', not 'postgresql+asyncpg://'
    sys_url = sys_url.replace("postgresql+asyncpg", "postgresql")

    print(f"Connecting to PostgreSQL server to check for '{db_name}'...")
    try:
        conn = await asyncpg.connect(sys_url)

        exists = await conn.fetchval(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'")

        if not exists:
            print(f"Database '{db_name}' does not exist. Creating it automatically...")
            # We cannot use standard parameterization for database names, so we format it safely
            await conn.execute(f'CREATE DATABASE "{db_name}"')
            print("Database created successfully!")
        else:
            print(f"Database '{db_name}' already exists.")

        await conn.close()
    except Exception as e:
        print(f"Failed to create database automatically: {e}")

if __name__ == "__main__":
    asyncio.run(create_database_if_not_exists())