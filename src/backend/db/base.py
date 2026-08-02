from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    """
    The declarative base class for all SQLAlchemy 2.0 models.
    Alembic will use this to autogenerate migrations.
    """
    pass