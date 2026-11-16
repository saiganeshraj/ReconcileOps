from sqlalchemy import create_engine

from alembic import context
from reconcileops.config import settings
from reconcileops.storage.models import Base

if context.is_offline_mode():
    context.configure(url=settings.DATABASE_URL, target_metadata=Base.metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(settings.DATABASE_URL).connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata)
        with context.begin_transaction():
            context.run_migrations()
