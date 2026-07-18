import csv
import hashlib
import io
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, time as dt_time, timedelta
from typing import Any


WDT_API_URL = os.getenv("WDT_API_URL", "https://openapi.huice.com/openapi/trade_query.php")
WDT_SID = os.getenv("WDT_SID", "")
WDT_APPKEY = os.getenv("WDT_APPKEY", "")
WDT_APPSECRET = os.getenv("WDT_APPSECRET", "")
WDT_PAGE_DELAY = float(os.getenv("WDT_PAGE_DELAY", "0.4"))
# 0 表示不设默认页数上限，按接口返回的 total_count 自动翻完。
WDT_MAX_PAGES = int(os.getenv("WDT_MAX_PAGES", "0"))
WDT_RATE_LIMIT_RETRIES = int(os.getenv("WDT_RATE_LIMIT_RETRIES", "4"))


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
        with urllib.request.urlopen(request, timeout=30) as response:
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
    while cursor < end_time:
        day_end = datetime.combine(cursor.date(), dt_time(23, 59, 59))
        chunk_end = min(end_time, day_end)
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
    return _first_number(order, ("real_amount", "receivable", "paid"))


def _goods_units(goods: dict[str, Any]) -> float:
    return _first_number(goods, ("num", "suite_num"))


def _goods_amount(goods: dict[str, Any], units: float) -> float:
    # 优先使用旺店通返回的商品行实付金额，缺失时再用单价乘数量估算。
    for field in ("paid", "share_amount", "oms_purchase_amount"):
        if goods.get(field) not in (None, ""):
            return _to_number(goods.get(field))
    unit_price = _first_number(goods, ("share_price", "price"))
    return unit_price * units


def _order_date(order: dict[str, Any], time_type: int) -> str:
    time_field = {1: "modified", 2: "trade_time", 3: "created"}.get(time_type, "modified")
    raw = order.get(time_field) or order.get("modified") or order.get("trade_time")
    if raw:
        return str(raw)[:10]
    return "未识别日期"


def _shop_info(order: dict[str, Any]) -> tuple[str, str]:
    shop_id = str(order.get("shop_id") or "")
    shop_name = str(order.get("shop_name") or order.get("fenxiao_shop_name") or "未识别店铺")
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
) -> dict[str, Any]:
    """从同一批订单生成总览、时间、店铺和商品四种可对账聚合。"""

    trade_cols, goods_cols, logistics_cols = collect_all_fields(orders)
    columns = [f"trade.{col}" for col in trade_cols]
    columns += [f"logistics.{col}" for col in logistics_cols]
    columns += [f"goods.{col}" for col in goods_cols]
    rows = [row for order in orders for row in flatten_order(order, trade_cols, goods_cols, logistics_cols)]

    daily: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"date": "", "order_count": 0, "order_amount": 0.0, "units": 0.0}
    )
    shops: dict[str, dict[str, Any]] = {}
    products: dict[str, dict[str, Any]] = {}
    order_amount = 0.0
    paid_amount = 0.0
    units_total = 0.0

    for order in orders:
        trade_key = _order_key(order)
        date_key = _order_date(order, time_type)
        amount = _order_amount(order)
        order_amount += amount
        paid_amount += _first_number(order, ("paid", "real_amount", "receivable"))

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
            line_amount = _goods_amount(goods, units)
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

        if not goods_list:
            order_units = _to_number(order.get("goods_count"))
        units_total += order_units
        day["units"] += order_units
        shop["units"] += order_units

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
    shop_rows.sort(key=lambda item: item["order_amount"], reverse=True)
    product_rows.sort(key=lambda item: item["order_amount"], reverse=True)

    return {
        "columns": columns,
        "rows": rows,
        "order_count": len(orders),
        "row_count": len(rows),
        "platform_ids": sorted({item.strip() for item in platform_ids if item.strip()}),
        "start_time": _format_datetime(start_time),
        "end_time": _format_datetime(end_time),
        "summary": {
            "order_count": len(orders),
            "detail_count": len(rows),
            "shop_count": len(shops),
            "product_count": len(products),
            "units": units_total,
            "order_amount": order_amount,
            "paid_amount": paid_amount,
            "avg_order_amount": order_amount / len(orders) if orders else 0.0,
        },
        "daily": daily_rows,
        "shops": shop_rows,
        "products": product_rows,
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

    orders = list(order_map.values())
    result = build_analysis(
        orders,
        start_time=start_time,
        end_time=end_time,
        platform_ids=sorted(selected_platforms),
        time_type=time_type,
    )
    result.update(
        {
            "api_total_count": api_total_count,
            "expected_count": expected_count,
            "complete": complete and not incomplete_windows,
            "incomplete_windows": incomplete_windows,
            "page_count": pages_fetched,
            "source_window_count": window_count,
        }
    )
    return result


def rows_to_csv(columns: list[str], rows: list[dict[str, Any]]) -> str:
    """将表格数据转换成 CSV 文本，前端可直接生成下载文件。"""

    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()
