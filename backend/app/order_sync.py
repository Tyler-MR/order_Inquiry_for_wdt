from __future__ import annotations

import json
import logging
import os
import threading
from datetime import date, datetime, time, timedelta
from time import monotonic
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import WdtOrder, WdtSyncRun
from app.wdt_client import (
    _load_shop_owner_map,
    _order_analysis_datetime,
    _owner_name,
    build_analysis,
    fetch_orders,
    normalize_shop_name,
)


logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))
DASHBOARD_SNAPSHOT_CACHE_SECONDS = max(
    0.0,
    float(os.getenv("WDT_DASHBOARD_SNAPSHOT_CACHE_SECONDS", "120")),
)
_dashboard_snapshot_cache: dict[tuple[Any, ...], tuple[float, list[dict[str, Any]], dict[str, Any]]] = {}
_dashboard_snapshot_cache_lock = threading.Lock()
HIDDEN_PDD_OWNER = "淘宝 李世豪"


def _parse_wdt_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                parsed = datetime.strptime(text, pattern)
                break
            except ValueError:
                continue
        else:
            return None
    return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed


def recent_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    current = now or datetime.now(LOCAL_TZ).replace(tzinfo=None)
    start_date = current.date() - timedelta(days=2)
    return datetime.combine(start_date, time.min), datetime.combine(current.date(), time.max).replace(microsecond=0)


def _sync_window(db: Session) -> tuple[datetime, datetime, str]:
    full_start, end_time = recent_window()
    sync_time_type = int(os.getenv("WDT_SYNC_TIME_TYPE", "1"))
    previous = db.scalar(
        select(WdtSyncRun)
        .where(WdtSyncRun.status == "success", WdtSyncRun.finished_at.is_not(None))
        .order_by(WdtSyncRun.finished_at.desc(), WdtSyncRun.id.desc())
    )

    # modified 时间支持增量同步；其他时间类型仍使用完整三天窗口，避免改变原有查询语义。
    if sync_time_type == 1 and previous and previous.finished_at:
        overlap_minutes = max(1, int(os.getenv("WDT_SYNC_OVERLAP_MINUTES", "15")))
        incremental_start = previous.finished_at - timedelta(minutes=overlap_minutes)
        return max(full_start, incremental_start), end_time, "incremental"
    return full_start, end_time, "full"


def _order_key(order: dict[str, Any]) -> str:
    platform_id = str(order.get("platform_id") or "").strip()
    trade_key = str(order.get("trade_id") or order.get("trade_no") or order.get("serial_no") or "").strip()
    return f"{platform_id}:{trade_key}" if trade_key else ""


def _upsert_orders(db: Session, orders: list[dict[str, Any]], synced_at: datetime) -> int:
    keyed_orders = [(key, order) for order in orders if (key := _order_key(order))]
    keys = [key for key, _ in keyed_orders]
    existing = {}
    if keys:
        existing = {
            item.order_key: item
            for item in db.scalars(select(WdtOrder).where(WdtOrder.order_key.in_(keys))).all()
        }

    for key, order in keyed_orders:
        item = existing.get(key)
        if item is None:
            item = WdtOrder(order_key=key, payload_json="{}")
            db.add(item)

        item.platform_id = str(order.get("platform_id") or "")
        item.trade_no = str(order.get("trade_no") or "")
        item.shop_name = normalize_shop_name(order.get("shop_name") or order.get("fenxiao_shop_name") or "")
        item.modified_at = _parse_wdt_datetime(order.get("modified"))
        item.trade_at = _parse_wdt_datetime(order.get("trade_time"))
        item.order_created_at = _parse_wdt_datetime(order.get("created"))
        item.payload_json = json.dumps(order, ensure_ascii=False, default=str)
        item.synced_at = synced_at

    return len(keyed_orders)


def _cleanup_expired_orders(db: Session, cutoff: datetime, time_type: int) -> int:
    """删除同步窗口之前的订单，保证本地库只保留近三天数据。"""

    time_column = _time_column(time_type)
    result = db.execute(delete(WdtOrder).where(time_column < cutoff))
    return int(result.rowcount or 0)


def sync_recent_orders() -> dict[str, Any]:
    """同步前天、昨天、今天的订单到 MySQL。"""

    started_at = datetime.now(LOCAL_TZ).replace(tzinfo=None)
    retention_start, _ = recent_window(started_at)
    sync_time_type = int(os.getenv("WDT_SYNC_TIME_TYPE", "1"))

    with SessionLocal() as db:
        start_time, end_time, sync_mode = _sync_window(db)
        run = WdtSyncRun(
            window_start=start_time,
            window_end=end_time,
            status="running",
            started_at=started_at,
        )
        db.add(run)
        db.commit()
        run_id = run.id

    try:
        orders, metadata = fetch_orders(
            start_time=start_time,
            end_time=end_time,
            platform_ids=[],
            page_size=int(os.getenv("WDT_SYNC_PAGE_SIZE", "500")),
            time_type=int(os.getenv("WDT_SYNC_TIME_TYPE", "1")),
            max_pages=None,
        )
        synced_at = datetime.now(LOCAL_TZ).replace(tzinfo=None)
        with SessionLocal() as db:
            saved_count = _upsert_orders(db, orders, synced_at)
            deleted_count = _cleanup_expired_orders(db, retention_start, sync_time_type)
            run = db.get(WdtSyncRun, run_id)
            if run:
                run.status = "success"
                run.fetched_count = saved_count
                run.finished_at = synced_at
            db.commit()

        clear_dashboard_snapshot_cache()

        result = {
            "status": "success",
            "synced_at": synced_at.isoformat(sep=" ", timespec="seconds"),
            "fetched_count": saved_count,
            "deleted_count": deleted_count,
            "page_count": metadata["page_count"],
            "sync_mode": sync_mode,
            "window_start": start_time.isoformat(sep=" "),
            "window_end": end_time.isoformat(sep=" "),
        }
        logger.info("WDT order sync completed: %s", result)
        return result
    except Exception as error:
        finished_at = datetime.now(LOCAL_TZ).replace(tzinfo=None)
        with SessionLocal() as db:
            run = db.get(WdtSyncRun, run_id)
            if run:
                run.status = "failed"
                run.finished_at = finished_at
                run.error_message = str(error)[:4000]
            db.commit()
        logger.exception("WDT order sync failed")
        raise


def _time_column(time_type: int):
    return {1: WdtOrder.modified_at, 2: WdtOrder.trade_at, 3: WdtOrder.order_created_at}.get(
        time_type,
        WdtOrder.modified_at,
    )


def clear_dashboard_snapshot_cache() -> None:
    """Drop the in-process order snapshot after a successful data sync."""

    with _dashboard_snapshot_cache_lock:
        _dashboard_snapshot_cache.clear()


def _load_dashboard_snapshot(
    db: Session,
    *,
    start_time: datetime,
    end_time: datetime,
    selected_platforms: list[str],
    time_type: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Load and decode a dashboard snapshot once per process/cache window."""

    cache_key = (start_time, end_time, tuple(selected_platforms), time_type)
    now = monotonic()
    if DASHBOARD_SNAPSHOT_CACHE_SECONDS > 0:
        with _dashboard_snapshot_cache_lock:
            cached = _dashboard_snapshot_cache.get(cache_key)
            if cached and now < cached[0]:
                return cached[1], cached[2]

    time_column = _time_column(time_type)
    statement = select(WdtOrder).where(time_column >= start_time, time_column <= end_time)
    if selected_platforms:
        statement = statement.where(WdtOrder.platform_id.in_(selected_platforms))
    statement = statement.order_by(time_column, WdtOrder.id)

    orders = [json.loads(item.payload_json) for item in db.scalars(statement).all()]
    owner_map = _load_shop_owner_map()
    orders = [
        order
        for order in orders
        if not (
            str(order.get("platform_id") or "").strip() == "39"
            and _owner_name(order, _order_shop_name(order), owner_map) == HIDDEN_PDD_OWNER
        )
    ]
    filter_options = _filter_options(orders)

    if DASHBOARD_SNAPSHOT_CACHE_SECONDS > 0:
        with _dashboard_snapshot_cache_lock:
            _dashboard_snapshot_cache[cache_key] = (
                now + DASHBOARD_SNAPSHOT_CACHE_SECONDS,
                orders,
                filter_options,
            )
    return orders, filter_options


def _clean_filter_values(values: Any) -> set[str]:
    if not isinstance(values, list):
        return set()
    return {str(value).strip() for value in values if str(value).strip()}


def _first_text(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = item.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _goods_sku(goods: dict[str, Any]) -> str:
    return _first_text(
        goods,
        (
            "sku_code",
            "sku_no",
            "sku编码",
            "spec_no",
            "api_spec_no",
            "goods_no",
            "api_goods_no",
        ),
    )


def _goods_product_name(goods: dict[str, Any]) -> str:
    return _first_text(goods, ("goods_name", "api_goods_name", "product_name"))


def _goods_brand(goods: dict[str, Any]) -> str:
    return _first_text(goods, ("brand", "brand_name", "goods_brand", "product_brand"))


def _order_shop_name(order: dict[str, Any]) -> str:
    return normalize_shop_name(_first_text(order, ("shop_name", "fenxiao_shop_name")))


def _date_layer(event_at: datetime | None) -> str:
    if event_at is None:
        return ""
    today = datetime.now(LOCAL_TZ).date()
    if event_at.date() == today:
        return "今日"
    if event_at.date() == today - timedelta(days=1):
        return "昨日"
    if event_at.date() == today - timedelta(days=2):
        return "前天"
    return ""


def _dashboard_comparison_date(
    *,
    fallback: date,
    filters: dict[str, Any] | None,
) -> date:
    """Return the latest explicitly selected date as the comparison base date."""
    date_layers = set(_clean_filter_values((filters or {}).get("date_layers")))
    if not date_layers:
        return fallback

    today = datetime.now(LOCAL_TZ).date()
    layer_offsets = {"今日": 0, "昨日": 1, "前天": 2}
    selected_dates = [
        today - timedelta(days=offset)
        for layer, offset in layer_offsets.items()
        if layer in date_layers
    ]
    return max(selected_dates, default=fallback)


def _filter_options(orders: list[dict[str, Any]]) -> dict[str, Any]:
    shops: set[str] = set()
    skus: set[str] = set()
    products: set[str] = set()
    brands: set[str] = set()
    owners: set[str] = set()
    owner_map = _load_shop_owner_map()
    for order in orders:
        shop_name = _order_shop_name(order)
        if shop_name:
            shops.add(shop_name)
        owners.add(_owner_name(order, shop_name, owner_map))
        for goods in order.get("goods_list") or []:
            sku = _goods_sku(goods)
            product_name = _goods_product_name(goods)
            brand = _goods_brand(goods)
            if sku:
                skus.add(sku)
            if product_name:
                products.add(product_name)
            if brand:
                brands.add(brand)
    return {
        "brands": sorted(brands)[:500],
        "sku_codes": sorted(skus)[:1000],
        "product_names": sorted(products)[:500],
        "shop_names": sorted(shops)[:500],
        "owner_names": sorted(owner for owner in owners if owner)[:500],
        "brand_available": bool(brands),
    }


def _apply_dashboard_filters(
    orders: list[dict[str, Any]],
    *,
    time_type: int,
    filters: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    filters = filters or {}
    brands = _clean_filter_values(filters.get("brand"))
    sku_codes = _clean_filter_values(filters.get("sku_codes"))
    product_names = _clean_filter_values(filters.get("product_names"))
    shop_names = _clean_filter_values(filters.get("shop_names"))
    owner_names = _clean_filter_values(filters.get("owner_names"))
    date_layers = _clean_filter_values(filters.get("date_layers"))
    time_truncated = bool(filters.get("time_truncated", True))
    local_now = datetime.now(LOCAL_TZ)
    current_hour = local_now.hour
    owner_map = _load_shop_owner_map() if owner_names else {}
    if not any((brands, sku_codes, product_names, shop_names, owner_names, date_layers)) and not time_truncated:
        return orders

    filtered: list[dict[str, Any]] = []
    for order in orders:
        event_at = _order_analysis_datetime(order, time_type)
        if (
            time_truncated
            and event_at is not None
            and event_at.date() == local_now.date()
            and event_at.hour >= current_hour
        ):
            continue
        if date_layers and _date_layer(event_at) not in date_layers:
            continue
        if shop_names and _order_shop_name(order) not in shop_names:
            continue
        if owner_names and _owner_name(order, _order_shop_name(order), owner_map) not in owner_names:
            continue

        goods_list = order.get("goods_list") or []
        if brands or sku_codes or product_names:
            matched_goods = []
            for goods in goods_list:
                brand = _goods_brand(goods)
                product_name = _goods_product_name(goods)
                # 当前旺店通订单接口没有独立品牌字段时，品牌筛选退化为商品名称关键词匹配。
                brand_match = (
                    not brands
                    or (brand and brand in brands)
                    or any(keyword.casefold() in product_name.casefold() for keyword in brands)
                )
                sku_match = not sku_codes or _goods_sku(goods) in sku_codes
                product_match = not product_names or product_name in product_names
                if brand_match and sku_match and product_match:
                    matched_goods.append(goods)
            if not matched_goods:
                continue
            if matched_goods is not goods_list:
                filtered_order = dict(order)
                filtered_order["goods_list"] = matched_goods
                filtered.append(filtered_order)
                continue
        filtered.append(order)
    return filtered


def read_order_analysis(
    db: Session,
    *,
    start_time: datetime,
    end_time: datetime,
    platform_ids: list[str],
    time_type: int,
    dashboard_filters: dict[str, Any] | None = None,
    include_rows: bool = True,
) -> dict[str, Any]:
    """从 MySQL 读取订单原始快照，并复用现有聚合逻辑返回看板数据。"""

    selected_platforms = sorted({item.strip() for item in platform_ids if item.strip()})
    orders, filter_options = _load_dashboard_snapshot(
        db,
        start_time=start_time,
        end_time=end_time,
        selected_platforms=selected_platforms,
        time_type=time_type,
    )
    filtered_orders = _apply_dashboard_filters(
        orders,
        time_type=time_type,
        filters=dashboard_filters,
    )
    comparison_date = _dashboard_comparison_date(
        fallback=end_time.date(),
        filters=dashboard_filters,
    )
    time_truncated = bool((dashboard_filters or {}).get("time_truncated", True))
    result = build_analysis(
        filtered_orders,
        start_time=start_time,
        end_time=end_time,
        platform_ids=selected_platforms,
        time_type=time_type,
        comparison_date=comparison_date,
        time_truncated=time_truncated,
        include_rows=include_rows,
    )
    result.update(
        {
            "api_total_count": len(filtered_orders),
            "expected_count": len(filtered_orders),
            "complete": True,
            "incomplete_windows": [],
            "page_count": 0,
            "source_window_count": (end_time.date() - start_time.date()).days + 1,
            "filter_options": filter_options,
            "active_filters": dashboard_filters or {},
            "pre_filter_order_count": len(orders),
        }
    )
    return result


def latest_sync(db: Session) -> dict[str, Any] | None:
    run = db.scalar(select(WdtSyncRun).order_by(WdtSyncRun.id.desc()))
    if run is None:
        return None
    return {
        "status": run.status,
        "synced_at": run.finished_at.isoformat(sep=" ", timespec="seconds") if run.finished_at else None,
        "fetched_count": run.fetched_count,
        "error_message": run.error_message,
    }
