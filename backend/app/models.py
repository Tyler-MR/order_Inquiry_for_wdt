from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """系统用户表：第一版只用于演示登录。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    password: Mapped[str] = mapped_column(String(128), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="admin", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Platform(Base):
    """平台表：抖音、快手、视频号、淘宝、拼多多、1688。"""

    __tablename__ = "platforms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    platform_type: Mapped[str] = mapped_column(String(64), nullable=False)
    focus: Mapped[str] = mapped_column(String(255), nullable=False)
    metric: Mapped[str] = mapped_column(String(64), nullable=False)


class Category(Base):
    """家清类目表：把业务类目沉淀为可浏览的数据。"""

    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    tag: Mapped[str] = mapped_column(String(32), nullable=False)


class Lead(Base):
    """合作咨询线索表：承接前端表单提交。"""

    __tablename__ = "leads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    company: Mapped[str] = mapped_column(String(128), nullable=False)
    contact: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(32), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="new", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WdtOrder(Base):
    """旺店通订单原始快照；保留 JSON 以兼容接口动态字段。"""

    __tablename__ = "order_query"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    order_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)
    platform_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False, default="")
    trade_no: Mapped[str] = mapped_column(String(128), index=True, nullable=False, default="")
    shop_name: Mapped[str] = mapped_column(String(255), index=True, nullable=False, default="")
    modified_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    trade_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    order_created_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    pay_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    consign_at: Mapped[datetime | None] = mapped_column(DateTime, index=True, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class WdtSyncRun(Base):
    """旺店通后台同步记录，方便看板显示最近同步状态和排查失败。"""

    __tablename__ = "wdt_sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    status: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    fetched_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)


class ShopOwnerMap(Base):
    """Normalized shop-to-owner reference data imported from the Windows workbook."""

    __tablename__ = "shop_owner_map"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    shop_name: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    owner_name: Mapped[str] = mapped_column(String(128), nullable=False)
    platform: Mapped[str] = mapped_column(String(64), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
