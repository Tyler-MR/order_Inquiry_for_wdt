import asyncio
import contextlib
import logging
import os
import secrets
from contextlib import asynccontextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import SQLAlchemyError

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
load_dotenv()

from app.profit_service import DEFAULT_DATA_DIR, ProfitDataStore, parse_date
from app.database import get_db
from app.order_events import OrderEventBus
from app.order_sync import latest_sync, read_order_analysis, sync_recent_orders
from app.schemas import WdtOrderQueryRequest, WdtOrderQueryResponse
from app.wdt_client import WdtApiError, WdtConfigError, query_orders


class ProfitQueryRequest(BaseModel):
    platform: str | None = Field(default=None, description="平台，例如拼多多、淘宝或全部")
    start_date: date | None = None
    end_date: date | None = None
    fields: list[str] = Field(default_factory=list, description="前端勾选展示的字段")
    value_filters: dict[str, Any] = Field(default_factory=dict, description="字段内值筛选")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=100, ge=1, le=500)


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=3, max_length=128)


class UserSession(BaseModel):
    username: str
    display_name: str
    department: str
    department_name: str
    allowed_platforms: list[str] | None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserSession


DEPARTMENT_POLICIES: dict[str, dict[str, Any]] = {
    "admin": {"name": "管理部", "allowed_platforms": None},
    "pdd": {"name": "拼多多部门", "allowed_platforms": ["拼多多"]},
    "taobao": {"name": "淘宝部门", "allowed_platforms": ["淘宝"]},
    "business": {"name": "商务部门", "allowed_platforms": ["抖音", "快手", "视频号"]},
    "1688": {"name": "1688部门", "allowed_platforms": ["1688"]},
}

# 演示账号。生产环境应改为数据库用户表、哈希密码和 JWT/Session 方案。
DEMO_USERS: dict[str, dict[str, str]] = {
    "demo_admin": {"password": "123456", "display_name": "系统管理员", "department": "admin"},
    "pdd_user": {"password": "123456", "display_name": "拼多多运营", "department": "pdd"},
    "taobao_user": {"password": "123456", "display_name": "淘宝运营", "department": "taobao"},
    "business_user": {"password": "123456", "display_name": "商务运营", "department": "business"},
    "1688_user": {"password": "123456", "display_name": "1688运营", "department": "1688"},
}

SESSION_STORE: dict[str, UserSession] = {}
logger = logging.getLogger(__name__)
order_event_bus = OrderEventBus()

DATA_DIR = Path(os.getenv("PROFIT_DATA_DIR", str(DEFAULT_DATA_DIR)))
profit_store = ProfitDataStore(DATA_DIR)

def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


async def run_order_sync_once() -> None:
    try:
        result = await asyncio.to_thread(sync_recent_orders)
        await order_event_bus.publish({"event": "orders.updated", **result})
    except Exception as error:
        logger.exception("后台旺店通同步失败")
        await order_event_bus.publish(
            {
                "event": "orders.sync_failed",
                "status": "failed",
                "message": str(error)[:1000],
            }
        )


async def order_sync_loop() -> None:
    if not _env_bool("WDT_SYNC_ENABLED", True):
        logger.info("WDT_SYNC_ENABLED=false，跳过后台订单同步")
        return

    if _env_bool("WDT_SYNC_ON_STARTUP", True):
        await run_order_sync_once()

    interval = max(60, int(os.getenv("WDT_SYNC_INTERVAL_SECONDS", "420")))
    while True:
        await asyncio.sleep(interval)
        await run_order_sync_once()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    sync_task = asyncio.create_task(order_sync_loop())
    try:
        yield
    finally:
        sync_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sync_task


app = FastAPI(
    title="旺店通订单查询与分析 API",
    description="查询旺店通订单，并提供店铺、商品、时间维度的经营分析。",
    version="1.0.0",
    lifespan=lifespan,
)

frontend_origin = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        frontend_origin,
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:5174",
        "http://localhost:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def build_user_session(username: str, user_record: dict[str, str]) -> UserSession:
    department = user_record["department"]
    policy = DEPARTMENT_POLICIES[department]
    return UserSession(
        username=username,
        display_name=user_record["display_name"],
        department=department,
        department_name=policy["name"],
        allowed_platforms=policy["allowed_platforms"],
    )


def get_current_user(authorization: str | None = Header(default=None)) -> UserSession:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录后再访问利润数据",
        )

    token = authorization.removeprefix("Bearer ").strip()
    user = SESSION_STORE.get(token)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
        )
    return user


def allowed_platform_set(user: UserSession) -> set[str] | None:
    if user.allowed_platforms is None:
        return None
    return set(user.allowed_platforms)


def ensure_platform_allowed(platform: str | None, user: UserSession) -> None:
    allowed = allowed_platform_set(user)
    if allowed is None or not platform or platform == "全部":
        return
    if platform not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"{user.department_name}无权查看平台：{platform}",
        )


@app.get("/api/health")
def health_check() -> dict[str, str]:
    return {"status": "ok", "service": "wdt-order-analysis-api"}


@app.post("/api/wdt/orders/query", response_model=WdtOrderQueryResponse)
def query_wdt_orders(payload: WdtOrderQueryRequest) -> dict[str, Any]:
    """查询旺店通订单，并返回订单明细及店铺、商品、时间三维聚合。"""

    if payload.start_time >= payload.end_time:
        raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")

    try:
        return query_orders(
            start_time=payload.start_time,
            end_time=payload.end_time,
            platform_ids=payload.platform_ids,
            page_size=payload.page_size,
            time_type=payload.time_type,
            max_pages=payload.max_pages,
        )
    except WdtConfigError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except WdtApiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"订单分析失败：{error}") from error


@app.post("/api/wdt/orders/dashboard", response_model=WdtOrderQueryResponse)
def read_wdt_dashboard(payload: WdtOrderQueryRequest, db=Depends(get_db)) -> dict[str, Any]:
    """从 MySQL 读取最近同步的订单，不直接访问旺店通。"""

    if payload.start_time >= payload.end_time:
        raise HTTPException(status_code=400, detail="开始时间必须早于结束时间")

    try:
        result = read_order_analysis(
            db,
            start_time=payload.start_time,
            end_time=payload.end_time,
            platform_ids=payload.platform_ids,
            time_type=payload.time_type,
            dashboard_filters=payload.dashboard_filters.model_dump(),
        )
        sync = latest_sync(db)
        result["last_synced_at"] = sync.get("synced_at") if sync else None
        result["sync_status"] = sync.get("status") if sync else "not_started"
        return result
    except SQLAlchemyError as error:
        logger.exception("读取本地订单数据失败")
        raise HTTPException(status_code=503, detail="本地 MySQL 暂不可用，请检查 DATABASE_URL 和数据库账号。") from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"读取本地订单数据失败：{error}") from error


@app.get("/api/wdt/orders/events")
async def order_events() -> StreamingResponse:
    """SSE：后台同步成功后向所有看板客户端广播轻量更新通知。"""

    return StreamingResponse(
        order_event_bus.stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/wdt/orders/sync")
async def trigger_wdt_sync() -> dict[str, Any]:
    """手动触发一次同步，供管理员排查或补数使用。"""

    try:
        result = await asyncio.to_thread(sync_recent_orders)
        await order_event_bus.publish({"event": "orders.updated", **result})
        return result
    except WdtConfigError as error:
        raise HTTPException(status_code=500, detail=str(error)) from error
    except WdtApiError as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"订单同步失败：{error}") from error


@app.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    user_record = DEMO_USERS.get(payload.username)
    if user_record is None or user_record["password"] != payload.password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
        )

    token = secrets.token_urlsafe(32)
    user = build_user_session(payload.username, user_record)
    SESSION_STORE[token] = user
    return LoginResponse(access_token=token, user=user)


@app.get("/api/auth/me", response_model=UserSession)
def get_me(user: UserSession = Depends(get_current_user)) -> UserSession:
    return user


@app.get("/api/profit/metadata")
def get_profit_metadata(user: UserSession = Depends(get_current_user)) -> dict:
    """返回当前登录用户有权查看的日期、平台、字段和文件列表。"""

    try:
        metadata = profit_store.metadata(allowed_platforms=allowed_platform_set(user))
        return {"user": user.model_dump(), **metadata}
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"读取利润表元数据失败：{error}") from error


@app.post("/api/profit/query")
def query_profit(
    payload: ProfitQueryRequest,
    user: UserSession = Depends(get_current_user),
) -> dict:
    """按日期、平台、字段和值筛选利润明细，并强制应用部门平台权限。"""

    if payload.start_date and payload.end_date and payload.start_date > payload.end_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    ensure_platform_allowed(payload.platform, user)

    try:
        return profit_store.query(
            platform=payload.platform,
            start_date=payload.start_date,
            end_date=payload.end_date,
            fields=payload.fields,
            value_filters=payload.value_filters,
            allowed_platforms=allowed_platform_set(user),
            page=payload.page,
            page_size=payload.page_size,
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"查询利润表失败：{error}") from error


@app.post("/api/profit/dashboard")
def get_profit_dashboard(
    payload: ProfitQueryRequest,
    user: UserSession = Depends(get_current_user),
) -> dict:
    """返回利润率看板所需的 KPI、趋势、负责人、店铺、商品和成本结构聚合数据。"""

    if payload.start_date and payload.end_date and payload.start_date > payload.end_date:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    ensure_platform_allowed(payload.platform, user)

    try:
        return profit_store.dashboard(
            platform=payload.platform,
            start_date=payload.start_date,
            end_date=payload.end_date,
            value_filters=payload.value_filters,
            allowed_platforms=allowed_platform_set(user),
        )
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"读取看板聚合数据失败：{error}") from error


@app.get("/api/profit/field-values")
def get_profit_field_values(
    field: str = Query(min_length=1),
    platform: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    keyword: str | None = None,
    limit: int = Query(default=200, ge=1, le=1000),
    user: UserSession = Depends(get_current_user),
) -> dict:
    """返回某个字段下的可选值，并强制应用部门平台权限。"""

    try:
        parsed_start = parse_date(start_date)
        parsed_end = parse_date(end_date)
        if parsed_start and parsed_end and parsed_start > parsed_end:
            raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
        ensure_platform_allowed(platform, user)

        return profit_store.field_values(
            field=field,
            platform=platform,
            start_date=parsed_start,
            end_date=parsed_end,
            keyword=keyword,
            allowed_platforms=allowed_platform_set(user),
            limit=limit,
        )
    except HTTPException:
        raise
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"读取字段值失败：{error}") from error


@app.post("/api/profit/reload")
def reload_profit_data(user: UserSession = Depends(get_current_user)) -> dict:
    """手动刷新缓存；刷新后仍然只返回当前用户有权查看的平台元数据。"""

    try:
        profit_store.reload()
        metadata = profit_store.metadata(allowed_platforms=allowed_platform_set(user))
        return {"message": "利润表缓存已刷新", "user": user.model_dump(), **metadata}
    except FileNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(status_code=500, detail=f"刷新利润表失败：{error}") from error
