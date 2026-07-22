import csv
import hashlib
import io
import json
import logging
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ShopOwnerMap


WDT_API_URL = os.getenv("WDT_API_URL", "https://openapi.huice.com/openapi/trade_query.php")
WDT_SID = os.getenv("WDT_SID", "")
WDT_APPKEY = os.getenv("WDT_APPKEY", "")
WDT_APPSECRET = os.getenv("WDT_APPSECRET", "")
WDT_PAGE_DELAY = float(os.getenv("WDT_PAGE_DELAY", "0.4"))
WDT_HTTP_TIMEOUT_SECONDS = max(1.0, float(os.getenv("WDT_HTTP_TIMEOUT_SECONDS", "240")))
WDT_WINDOW_HOURS = max(1, min(24, int(os.getenv("WDT_WINDOW_HOURS", "6"))))
# 0 表示不设默认页数上限，按接口返回的 total_count 自动翻完。
WDT_MAX_PAGES = int(os.getenv("WDT_MAX_PAGES", "0"))
WDT_RATE_LIMIT_RETRIES = int(os.getenv("WDT_RATE_LIMIT_RETRIES", "4"))
LOCAL_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))
_shop_owner_cache: dict[str, str] = {}
_shop_owner_cache_loaded_at = 0.0
SHOP_OWNER_CACHE_SECONDS = max(0.0, float(os.getenv("WDT_SHOP_OWNER_CACHE_SECONDS", "30")))
logger = logging.getLogger(__name__)


class WdtConfigError(RuntimeError):
    """旺店通鉴权配置缺失。"""


class WdtApiError(RuntimeError):
    """旺店通接口返回业务错误或网络错误。"""


def _ensure_config() -> None:
    missing = [
        name
        for name, value in {
            "WDT_SID": WDT_SID,
            "WDT_APPKEY": WDT_APPKEY,
            "WDT_APPSECRET": WDT_APPSECRET,
        }.items()
        if not value
    ]
    if missing:
        raise WdtConfigError(f"缺少旺店通配置：{', '.join(missing)}")


def sign(params: dict[str, Any], secret: str) -> str:
    """按旺店通长度规则生成 MD5 签名，中文字段必须按 UTF-8 字节计数。"""

    parts: list[str] = []
    keys = sorted(params.keys())
    for index, key in enumerate(keys):
        value = str(params[key])
        key_len = len(key.encode("utf-8"))
        value_len = len(value.encode("utf-8"))
        suffix = ";" if index < len(keys) - 1 else ""
        parts.append(f"{key_len:02d}-{key}:{value_len:04d}-{value}{suffix}")

    raw = "".join(parts) + secret
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def call_api(biz_params: dict[str, Any]) -> dict[str, Any]:
    """调用旺店通接口，并把网络异常转换成前端可读的业务异常。"""

    _ensure_config()
    params = {
        "sid": WDT_SID,
        "appkey": WDT_APPKEY,
        "timestamp": int(time.time()),
        **biz_params,
    }
    params["sign"] = sign(params, WDT_APPSECRET)

    data = urllib.parse.urlencode(params).encode("utf-8")
    request = urllib.request.Request(WDT_API_URL, data=data, method="POST")
    request.add_header("Content-Type", "application/x-www-form-urlencoded; charset=utf-8")

    try:
        with urllib.request.urlopen(request, timeout=WDT_HTTP_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise WdtApiError(f"旺店通 HTTP 错误：{error.code}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise WdtApiError("旺店通接口暂时无法访问，请检查网络或稍后重试") from error


def call_page_with_retry(biz_params: dict[str, Any]) -> dict[str, Any]:
    """对旺店通分页接口的限流响应做退避重试，避免长分页任务中途失败。"""

    for attempt in range(WDT_RATE_LIMIT_RETRIES + 1):
        result = call_api(biz_params)
        try:
            code = int(result.get("code"))
        except (TypeError, ValueError):
            code = -1
        if code != 1012:
            return result
        if attempt >= WDT_RATE_LIMIT_RETRIES:
            return result
        time.sleep(max(WDT_PAGE_DELAY, 1.0) * (2**attempt))
    raise WdtApiError("旺店通分页请求重试失败")


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, (list, dict))


def collect_all_fields(orders: list[dict[str, Any]]) -> tuple[list[str], list[str], list[str]]:
    """动态收集订单、商品、物流字段，避免新增字段后导出丢列。"""

    trade_keys: set[str] = set()
    goods_keys: set[str] = set()
    logistics_keys: set[str] = set()

    for order in orders:
        trade_keys.update(key for key, value in order.items() if _is_scalar(value))
        for goods in order.get("goods_list") or []:
            goods_keys.update(key for key, value in goods.items() if _is_scalar(value))
        for logistics in order.get("logistics_list") or []:
            logistics_keys.update(key for key, value in logistics.items() if _is_scalar(value))

    return sorted(trade_keys), sorted(goods_keys), sorted(logistics_keys)


def flatten_order(
    order: dict[str, Any],
    trade_cols: list[str],
    goods_cols: list[str],
    logistics_cols: list[str],
) -> list[dict[str, Any]]:
    """把订单展开成多行，每个商品明细一行，方便表格预览和 CSV 导出。"""

    base: dict[str, Any] = {}
    for col in trade_cols:
        base[f"trade.{col}"] = order.get(col) or ""

    logistics_list = order.get("logistics_list") or []
    first_logistics = logistics_list[0] if logistics_list else {}
    for col in logistics_cols:
        base[f"logistics.{col}"] = first_logistics.get(col) or ""

    goods_list = order.get("goods_list") or []
    if not goods_list:
        row = dict(base)
        for col in goods_cols:
            row[f"goods.{col}"] = ""
        return [row]

    rows: list[dict[str, Any]] = []
    for goods in goods_list:
        row = dict(base)
        for col in goods_cols:
            row[f"goods.{col}"] = goods.get(col) or ""
        rows.append(row)
    return rows


def _format_datetime(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _day_ranges(start_time: datetime, end_time: datetime) -> list[tuple[datetime, datetime]]:
    """旺店通单次查询不能跨过约 24 小时，因此按自然日拆分请求。"""

    ranges: list[tuple[datetime, datetime]] = []
    cursor = start_time
    window_delta = timedelta(hours=WDT_WINDOW_HOURS)
    while cursor <= end_time:
        chunk_end = min(end_time, cursor + window_delta - timedelta(seconds=1))
        ranges.append((cursor, chunk_end))
        if chunk_end >= end_time:
            break
        cursor = chunk_end + timedelta(seconds=1)
    return ranges


def _order_key(order: dict[str, Any]) -> str:
    return str(order.get("trade_id") or order.get("trade_no") or order.get("serial_no") or "")


def _to_number(value: Any, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return default


def _first_number(record: dict[str, Any], fields: tuple[str, ...]) -> float:
    for field in fields:
        value = record.get(field)
        if value is not None and value != "":
            return _to_number(value)
    return 0.0


def _order_amount(order: dict[str, Any]) -> float:
    """Return the order total calculated from its goods-level paid amounts."""
    goods_list = order.get("goods_list") or []
    return sum(_goods_amount(goods) for goods in goods_list)


def _goods_units(goods: dict[str, Any]) -> float:
    return _first_number(goods, ("num", "suite_num"))


def _goods_amount(goods: dict[str, Any]) -> float:
    # 所有看板金额统一使用旺店通返回的商品行分摊金额。
    """Return only the goods.share_amount amount; missing values contribute zero."""
    return _to_number(goods.get("share_amount"))


def _order_date(order: dict[str, Any], time_type: int) -> str:
    event_at = _order_analysis_datetime(order, time_type)
    if event_at:
        return event_at.date().isoformat()
    return "未识别日期"


def _parse_analysis_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    if text.startswith("0000-"):
        return None
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


def _order_analysis_datetime(order: dict[str, Any], time_type: int) -> datetime | None:
    """按看板选择的时间字段解析订单事件时间，缺失时回退到可用时间。"""

    selected_field = {
        1: "modified",
        2: "trade_time",
        3: "created",
        4: "pay_time",
        5: "consign_time",
    }.get(time_type, "pay_time")
    fallback_fields = ("trade_time", "pay_time", "modified", "created")
    fields = (selected_field, *fallback_fields)
    for field in fields:
        parsed = _parse_analysis_datetime(order.get(field))
        if parsed:
            return parsed
    return None


def _load_shop_owner_map() -> dict[str, str]:
    """从 MySQL 读取店铺-负责人映射，并在进程内短暂缓存。"""

    return _load_shop_owner_map_from_db()


def _load_shop_owner_map_from_db() -> dict[str, str]:
    """Load the normalized shop-owner mapping from the database."""

    global _shop_owner_cache, _shop_owner_cache_loaded_at
    now = time.monotonic()
    if (
        _shop_owner_cache
        and SHOP_OWNER_CACHE_SECONDS > 0
        and now - _shop_owner_cache_loaded_at < SHOP_OWNER_CACHE_SECONDS
    ):
        return _shop_owner_cache

    try:
        with SessionLocal() as db:
            rows = db.scalars(
                select(ShopOwnerMap).where(ShopOwnerMap.is_active.is_(True))
            ).all()
            mapping: dict[str, str] = {}
            for row in rows:
                shop_name = normalize_shop_name(row.shop_name)
                owner_name = row.owner_name.strip()
                if shop_name and owner_name:
                    mapping[shop_name] = owner_name
        _shop_owner_cache = mapping
        _shop_owner_cache_loaded_at = now
    except Exception:
        logger.exception("Failed to load shop-owner mapping from MySQL")
        if not _shop_owner_cache:
            return {}
    return _shop_owner_cache


def _owner_name(order: dict[str, Any], shop_name: str, owner_map: dict[str, str]) -> str:
    direct_owner = str(order.get("owner_name") or order.get("salesman_name") or "").strip()
    return direct_owner or owner_map.get(shop_name, "未分配")


def normalize_shop_name(value: Any) -> str:
    """统一订单与店铺负责人表中的店铺名称。

    旺店通订单里的拼多多店铺有时带有 ``拼多多-`` 前缀或括号备注，
    而负责人映射表使用的是基础店铺名。这里删除前缀，并在中文/英文
    左括号处截断，保留清洗后的第 0 项，保证各看板使用同一店铺维度。
    """

    text = str(value or "").strip().replace("拼多多-", "")
    return text.split("（", 1)[0].split("(", 1)[0].strip()


def _growth_pct(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return (current - previous) / previous * 100


def _new_comparison_entry(**extra: Any) -> dict[str, Any]:
    return {
        **extra,
        "today_amount": 0.0,
        "yesterday_amount": 0.0,
        "today_units": 0.0,
        "yesterday_units": 0.0,
        "today_order_keys": set(),
        "yesterday_order_keys": set(),
    }


def _add_comparison_value(
    entry: dict[str, Any],
    bucket: str,
    *,
    amount: float,
    units: float,
    order_key: str,
) -> None:
    entry[f"{bucket}_amount"] += amount
    entry[f"{bucket}_units"] += units
    entry[f"{bucket}_order_keys"].add(order_key)


def _finalize_comparison_entry(entry: dict[str, Any]) -> dict[str, Any]:
    item = dict(entry)
    today_keys = item.pop("today_order_keys")
    yesterday_keys = item.pop("yesterday_order_keys")
    item["today_order_count"] = len(today_keys)
    item["yesterday_order_count"] = len(yesterday_keys)
    item["amount_delta"] = item["today_amount"] - item["yesterday_amount"]
    item["units_delta"] = item["today_units"] - item["yesterday_units"]
    item["order_delta"] = item["today_order_count"] - item["yesterday_order_count"]
    item["amount_growth_pct"] = _growth_pct(item["today_amount"], item["yesterday_amount"])
    item["units_growth_pct"] = _growth_pct(item["today_units"], item["yesterday_units"])
    item["order_growth_pct"] = _growth_pct(item["today_order_count"], item["yesterday_order_count"])
    return item


def _shop_info(order: dict[str, Any]) -> tuple[str, str]:
    shop_id = str(order.get("shop_id") or "")
    shop_name = normalize_shop_name(order.get("shop_name") or order.get("fenxiao_shop_name") or "未识别店铺")
    return shop_id, shop_name


def _product_info(goods: dict[str, Any]) -> tuple[str, str, str]:
    product_no = str(goods.get("goods_no") or goods.get("spec_no") or goods.get("api_goods_no") or "")
    product_name = str(goods.get("goods_name") or goods.get("api_goods_name") or "未识别商品")
    spec_name = str(goods.get("spec_name") or goods.get("api_spec_name") or "")
    return product_no, product_name, spec_name


def build_analysis(
    orders: list[dict[str, Any]],
    *,
    start_time: datetime,
    end_time: datetime,
    platform_ids: list[str],
    time_type: int,
    comparison_date: date | None = None,
    time_truncated: bool = True,
    include_rows: bool = True,
    row_preview_limit: int = 100,
) -> dict[str, Any]:
    """从同一批订单生成总览、时间、店铺和商品四种可对账聚合。"""

    trade_cols, goods_cols, logistics_cols = collect_all_fields(orders)
    columns = [f"trade.{col}" for col in trade_cols]
    columns += [f"logistics.{col}" for col in logistics_cols]
    columns += [f"goods.{col}" for col in goods_cols]
    row_count = sum(max(1, len(order.get("goods_list") or [])) for order in orders)
    if include_rows:
        rows = [
            row
            for order in orders
            for row in flatten_order(order, trade_cols, goods_cols, logistics_cols)
        ]
    else:
        rows = []
        preview_limit = max(0, row_preview_limit)
        for order in orders:
            if len(rows) >= preview_limit:
                break
            rows.extend(
                flatten_order(order, trade_cols, goods_cols, logistics_cols)[
                    : preview_limit - len(rows)
                ]
            )

    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"date": "", "order_count": 0, "order_amount": 0.0, "units": 0.0}
    )
    shops: dict[str, dict[str, Any]] = {}
    products: dict[str, dict[str, Any]] = {}
    shop_comparison: dict[str, dict[str, Any]] = {}
    product_comparison: dict[str, dict[str, Any]] = {}
    owner_comparison: dict[str, dict[str, Any]] = {}
    hourly_comparison = {
        hour: _new_comparison_entry(hour=hour, label=f"{hour:02d}:00") for hour in range(24)
    }
    comparison_totals = _new_comparison_entry()
    owner_map = _load_shop_owner_map()
    comparison_date = comparison_date or end_time.date()
    local_today = datetime.now(LOCAL_TZ).date()
    local_current_hour = datetime.now(LOCAL_TZ).hour
    comparison_cutoff_hour = (
        local_current_hour
        if comparison_date == local_today and time_truncated
        else 24
    )
    comparison_previous_date = comparison_date - timedelta(days=1)
    comparison_day_labels = {0: "今日", 1: "昨日", 2: "前天"}
    comparison_today_label = comparison_day_labels.get(
        (local_today - comparison_date).days,
        comparison_date.isoformat(),
    )
    comparison_yesterday_label = comparison_day_labels.get(
        (local_today - comparison_previous_date).days,
        comparison_previous_date.isoformat(),
    )
    matched_owner_orders = 0
    order_amount = 0.0
    paid_amount = 0.0
    units_total = 0.0

    for order in orders:
        trade_key = _order_key(order)
        date_key = _order_date(order, time_type)
        amount = _order_amount(order)
        event_at = _order_analysis_datetime(order, time_type)
        comparison_bucket: str | None = None
        if event_at and event_at.hour < comparison_cutoff_hour:
            if event_at.date() == comparison_date:
                comparison_bucket = "today"
            elif event_at.date() == comparison_previous_date:
                comparison_bucket = "yesterday"
        order_amount += amount
        paid_amount += amount

        day = daily[date_key]
        day["date"] = date_key
        day["order_count"] += 1
        day["order_amount"] += amount

        shop_id, shop_name = _shop_info(order)
        shop_key = f"{shop_id}|{shop_name}"
        shop = shops.setdefault(
            shop_key,
            {
                "shop_id": shop_id,
                "shop_name": shop_name,
                "order_count": 0,
                "order_amount": 0.0,
                "units": 0.0,
                "product_count": 0,
                "product_keys": set(),
            },
        )
        shop["order_count"] += 1
        shop["order_amount"] += amount

        goods_list = order.get("goods_list") or []
        order_units = 0.0
        for goods in goods_list:
            units = _goods_units(goods)
            line_amount = _goods_amount(goods)
            order_units += units
            product_no, product_name, spec_name = _product_info(goods)
            product_key = f"{product_no}|{product_name}|{spec_name}"
            product = products.setdefault(
                product_key,
                {
                    "product_no": product_no,
                    "product_name": product_name,
                    "spec_name": spec_name,
                    "order_count": 0,
                    "units": 0.0,
                    "order_amount": 0.0,
                    "shop_count": 0,
                    "order_keys": set(),
                    "shop_keys": set(),
                },
            )
            product["units"] += units
            product["order_amount"] += line_amount
            product["order_keys"].add(trade_key)
            product["shop_keys"].add(shop_key)
            shop["product_keys"].add(product_key)

            if comparison_bucket:
                comparison_product = product_comparison.setdefault(
                    product_key,
                    _new_comparison_entry(
                        product_no=product_no,
                        product_name=product_name,
                        spec_name=spec_name,
                    ),
                )
                _add_comparison_value(
                    comparison_product,
                    comparison_bucket,
                    amount=line_amount,
                    units=units,
                    order_key=trade_key,
                )

        if not goods_list:
            order_units = _to_number(order.get("goods_count"))
        units_total += order_units
        day["units"] += order_units
        shop["units"] += order_units

        if comparison_bucket and event_at:
            comparison_shop = shop_comparison.setdefault(
                shop_key,
                _new_comparison_entry(shop_id=shop_id, shop_name=shop_name),
            )
            _add_comparison_value(
                comparison_shop,
                comparison_bucket,
                amount=amount,
                units=order_units,
                order_key=trade_key,
            )

            owner_name = _owner_name(order, shop_name, owner_map)
            if owner_name != "未分配":
                matched_owner_orders += 1
            comparison_owner = owner_comparison.setdefault(
                owner_name,
                _new_comparison_entry(owner_name=owner_name),
            )
            _add_comparison_value(
                comparison_owner,
                comparison_bucket,
                amount=amount,
                units=order_units,
                order_key=trade_key,
            )
            _add_comparison_value(
                comparison_totals,
                comparison_bucket,
                amount=amount,
                units=order_units,
                order_key=trade_key,
            )
            _add_comparison_value(
                hourly_comparison[event_at.hour],
                comparison_bucket,
                amount=amount,
                units=order_units,
                order_key=trade_key,
            )

    for product in products.values():
        product["order_count"] = len(product.pop("order_keys"))
        product["shop_count"] = len(product.pop("shop_keys"))
    for shop in shops.values():
        shop["product_count"] = len(shop.pop("product_keys"))

    def finalize(item: dict[str, Any]) -> dict[str, Any]:
        item = dict(item)
        item["avg_order_amount"] = (
            item["order_amount"] / item["order_count"] if item.get("order_count") else 0.0
        )
        return item

    daily_rows = [finalize(item) for item in sorted(daily.values(), key=lambda item: item["date"])]
    shop_rows = [finalize(item) for item in shops.values()]
    product_rows = [finalize(item) for item in products.values()]
    shop_comparison_rows = [_finalize_comparison_entry(item) for item in shop_comparison.values()]
    product_comparison_rows = [_finalize_comparison_entry(item) for item in product_comparison.values()]
    owner_comparison_rows = [_finalize_comparison_entry(item) for item in owner_comparison.values()]
    comparison_summary = _finalize_comparison_entry(comparison_totals)
    hourly_rows = [
        _finalize_comparison_entry(item)
        for hour, item in hourly_comparison.items()
        if comparison_date != local_today or hour < comparison_cutoff_hour
    ]
    cumulative_today_amount = 0.0
    cumulative_yesterday_amount = 0.0
    cumulative_today_units = 0.0
    cumulative_yesterday_units = 0.0
    for item in hourly_rows:
        cumulative_today_amount += item["today_amount"]
        cumulative_yesterday_amount += item["yesterday_amount"]
        cumulative_today_units += item["today_units"]
        cumulative_yesterday_units += item["yesterday_units"]
        item["today_cumulative_amount"] = cumulative_today_amount
        item["yesterday_cumulative_amount"] = cumulative_yesterday_amount
        item["today_cumulative_units"] = cumulative_today_units
        item["yesterday_cumulative_units"] = cumulative_yesterday_units
        item["cumulative_amount_delta"] = cumulative_today_amount - cumulative_yesterday_amount
        item["cumulative_units_delta"] = cumulative_today_units - cumulative_yesterday_units
        item["cumulative_amount_growth_pct"] = _growth_pct(
            cumulative_today_amount,
            cumulative_yesterday_amount,
        )
        item["cumulative_units_growth_pct"] = _growth_pct(
            cumulative_today_units,
            cumulative_yesterday_units,
        )
    shop_rows.sort(key=lambda item: item["order_amount"], reverse=True)
    product_rows.sort(key=lambda item: item["order_amount"], reverse=True)
    shop_comparison_rows.sort(key=lambda item: (item["today_amount"], item["yesterday_amount"]), reverse=True)
    product_comparison_rows.sort(key=lambda item: (item["today_amount"], item["yesterday_amount"]), reverse=True)
    owner_comparison_rows.sort(key=lambda item: (item["today_amount"], item["yesterday_amount"]), reverse=True)
    for index, item in enumerate(shop_comparison_rows, start=1):
        item["rank"] = index
    for index, item in enumerate(product_comparison_rows, start=1):
        item["rank"] = index
    for index, item in enumerate(owner_comparison_rows, start=1):
        item["rank"] = index

    return {
        "columns": columns,
        "rows": rows,
        "order_count": len(orders),
        "row_count": row_count,
        "rows_complete": include_rows,
        "platform_ids": sorted({item.strip() for item in platform_ids if item.strip()}),
        "start_time": _format_datetime(start_time),
        "end_time": _format_datetime(end_time),
        "summary": {
            "order_count": len(orders),
            "detail_count": row_count,
            "shop_count": len(shops),
            "product_count": len(products),
            "units": units_total,
            "order_amount": order_amount,
            "paid_amount": paid_amount,
            "avg_order_amount": order_amount / len(orders) if orders else 0.0,
            "today_order_count": comparison_summary["today_order_count"],
            "yesterday_order_count": comparison_summary["yesterday_order_count"],
            "today_amount": comparison_summary["today_amount"],
            "yesterday_amount": comparison_summary["yesterday_amount"],
            "today_units": comparison_summary["today_units"],
            "yesterday_units": comparison_summary["yesterday_units"],
            "amount_delta": comparison_summary["amount_delta"],
            "amount_growth_pct": comparison_summary["amount_growth_pct"],
            "order_delta": comparison_summary["order_delta"],
            "order_growth_pct": comparison_summary["order_growth_pct"],
            "units_delta": comparison_summary["units_delta"],
            "units_growth_pct": comparison_summary["units_growth_pct"],
        },
        "daily": daily_rows,
        "shops": shop_rows,
        "products": product_rows,
        "hourly": hourly_rows,
        "shop_comparison": shop_comparison_rows,
        "product_comparison": product_comparison_rows,
        "owner_comparison": owner_comparison_rows,
        "comparison": {
            "today": comparison_date.isoformat(),
            "yesterday": comparison_previous_date.isoformat(),
            "today_label": comparison_today_label,
            "yesterday_label": comparison_yesterday_label,
            "cutoff_hour": comparison_cutoff_hour,
            "analysis_time_field": {
                1: "modified",
                2: "trade_time",
                3: "created",
                4: "pay_time",
                5: "consign_time",
            }.get(time_type, "pay_time"),
            "owner_mapping_source": "店铺信息表",
            "owner_mapping_coverage_pct": (
                matched_owner_orders
                / (comparison_summary["today_order_count"] + comparison_summary["yesterday_order_count"])
                * 100
                if comparison_summary["today_order_count"] + comparison_summary["yesterday_order_count"]
                else 0.0
            ),
        },
    }


def fetch_orders(
    *,
    start_time: datetime,
    end_time: datetime,
    platform_ids: list[str],
    page_size: int,
    time_type: int = 1,
    max_pages: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """分页拉取原始订单，供手动查询和后台同步共同使用。"""

    if start_time >= end_time:
        raise ValueError("开始时间必须早于结束时间")

    # 只有显式传入 max_pages，或环境变量设置了大于 0 的安全上限时才限制页数。
    # 默认值为 0，避免把“最多取 50 页”误当成完整结果。
    page_limit = max_pages if max_pages is not None else (WDT_MAX_PAGES or None)
    selected_platforms = {item.strip() for item in platform_ids if item.strip()}
    order_map: dict[str, dict[str, Any]] = {}
    api_total_count = 0
    pages_fetched = 0
    window_count = 0
    expected_count = 0
    complete = True
    incomplete_windows: list[str] = []

    for chunk_start, chunk_end in _day_ranges(start_time, end_time):
        window_count += 1
        page_no = 0
        window_total_count: int | None = None
        window_pages_fetched = 0

        while True:
            if page_limit is not None and window_pages_fetched >= page_limit:
                complete = False
                incomplete_windows.append(
                    f"{_format_datetime(chunk_start)} ~ {_format_datetime(chunk_end)}"
                )
                break

            result = call_page_with_retry(
                {
                    "start_time": _format_datetime(chunk_start),
                    "end_time": _format_datetime(chunk_end),
                    "time_type": time_type,
                    "page_no": page_no,
                    "page_size": page_size,
                }
            )

            raw_code = result.get("code")
            code = int(raw_code) if raw_code is not None else -1
            if code != 0:
                if code == 1012:
                    raise WdtApiError("旺店通接口频率超限，请稍后再试")
                raise WdtApiError(
                    f"旺店通接口错误：code={code}, message={result.get('message', '')}"
                )

            raw_trades = result.get("trades") or []
            if page_no == 0:
                raw_total_count = result.get("total_count")
                window_total_count = (
                    int(raw_total_count)
                    if raw_total_count not in (None, "")
                    else None
                )
                if window_total_count is not None:
                    api_total_count += window_total_count
                    expected_count += window_total_count
            pages_fetched += 1
            window_pages_fetched += 1

            for trade in raw_trades:
                if selected_platforms and str(trade.get("platform_id", "")).strip() not in selected_platforms:
                    continue
                key = _order_key(trade) or f"{chunk_start.isoformat()}-{len(order_map)}"
                order_map.setdefault(key, trade)

            if window_total_count is not None:
                required_pages = math.ceil(window_total_count / page_size) if window_total_count else 0
                if window_pages_fetched >= required_pages:
                    break
            elif len(raw_trades) < page_size:
                break

            if not raw_trades:
                complete = False
                incomplete_windows.append(
                    f"{_format_datetime(chunk_start)} ~ {_format_datetime(chunk_end)}"
                )
                break

            page_no += 1
            time.sleep(WDT_PAGE_DELAY)
        time.sleep(WDT_PAGE_DELAY)

    if incomplete_windows:
        limit_hint = f"当前页数上限为 {page_limit}，请留空或调大 max_pages。" if page_limit else "请稍后重试。"
        raise WdtApiError(
            "订单分页未完整拉取，系统已拒绝返回半份结果。"
            f"涉及时间窗口：{incomplete_windows[0]}。{limit_hint}"
        )

    return list(order_map.values()), {
        "api_total_count": api_total_count,
        "expected_count": expected_count,
        "complete": complete and not incomplete_windows,
        "incomplete_windows": incomplete_windows,
        "page_count": pages_fetched,
        "source_window_count": (end_time.date() - start_time.date()).days + 1,
        "request_window_count": window_count,
        "platform_ids": sorted(selected_platforms),
    }


def query_orders(
    *,
    start_time: datetime,
    end_time: datetime,
    platform_ids: list[str],
    page_size: int,
    time_type: int = 1,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """分页查询订单，自动按天拆分，再返回表格和三维分析数据。"""

    orders, metadata = fetch_orders(
        start_time=start_time,
        end_time=end_time,
        platform_ids=platform_ids,
        page_size=page_size,
        time_type=time_type,
        max_pages=max_pages,
    )
    result = build_analysis(
        orders,
        start_time=start_time,
        end_time=end_time,
        platform_ids=metadata["platform_ids"],
        time_type=time_type,
    )
    result.update(metadata)
    return result


def rows_to_csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """将表格数据转换成 CSV 文本，前端可直接生成下载文件。"""

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
