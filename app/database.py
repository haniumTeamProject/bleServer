from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# 요청마다 DB 세션을 하나 열어주고, 끝나면 닫아주는 FastAPI 의존성.
# Spring Data JPA가 알아서 처리해주던 걸 여기서는 직접 관리해야 함.
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
