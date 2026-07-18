import os
from collections.abc import Generator

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

# 生产项目建议把 DATABASE_URL 放在服务器环境变量或密钥管理系统中。
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "mysql+pymysql://root:password@127.0.0.1:3306/cleaning_platform?charset=utf8mb4",
)

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_recycle=3600,
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    """SQLAlchemy 2.x 声明式模型基类。"""


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖函数：每次请求创建一个数据库会话，用完自动关闭。"""

    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
