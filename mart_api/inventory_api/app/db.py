import os
from app import settings
from sqlmodel import SQLModel, create_engine, Session


# only needed for psycopg 3 - replace postgresql
# with postgresql+psycopg in settings.DATABASE_URL
URL = settings.TEST_DATABASE_URL if os.getenv("TESTING") == "1" else settings.DATABASE_URL
connection_string = str(URL).replace(
    "postgresql", "postgresql+psycopg"
)


# recycle connections after 5 minutes
# to correspond with the compute scale down
engine = create_engine(
    connection_string, connect_args={}, pool_recycle=300
)

#engine = create_engine(
#    connection_string, connect_args={"sslmode": "require"}, pool_recycle=300
#)


def create_db_and_tables()->None:
    SQLModel.metadata.create_all(engine)

def get_session():
    with Session(engine) as session:
        yield session    