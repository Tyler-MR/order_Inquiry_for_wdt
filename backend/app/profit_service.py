from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_DATA_DIR = Path("data/profit")

# 只匹配“平台 + 链接利润/链接利润率 + start-end.xlsx”，并在代码里继续要求 start == end。
PROFIT_FILE_PATTERN = re.compile(
    r"^(?P<platform>拼多多|淘宝|抖音|快手|视频号|1688)链接利润率?(?P<start>\d{4}-\d{2}-\d{2})-(?P<end>\d{4}-\d{2}-\d{2})\.xlsx$"
)

PERCENT_KEYWORDS = ("率", "占比")
SYSTEM_COLUMNS = ["平台", "数据日期", "来源文件"]


@dataclass(frozen=True)
class ProfitFile:
    platform: str
    data_date: date
    path: Path


def parse_date(value: str | date | None) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def list_profit_files(data_dir: Path = DEFAULT_DATA_DIR) -> list[ProfitFile]:
    if not data_dir.exists():
        raise FileNotFoundError(f"数据目录不存在：{data_dir}")

    files: list[ProfitFile] = []
    for path in data_dir.iterdir():
        if not path.is_file():
            continue
        match = PROFIT_FILE_PATTERN.match(path.name)
        if not match:
            continue

        start_date = parse_date(match.group("start"))
        end_date = parse_date(match.group("end"))
        if start_date != end_date:
            continue

        files.append(
            ProfitFile(
                platform=match.group("platform"),
                data_date=start_date,
                path=path,
            )
        )

    return sorted(files, key=lambda item: (item.data_date, item.platform, item.path.name))


def _normalize_cell(value: Any) -> Any:
    """把 pandas/numpy 里的 NaN、inf 转成前端 JSON 更好处理的 None。"""

    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def _clean_percent_value(value: Any) -> float | None:
    """把 60.08%、nan%、-inf% 等百分比文本统一转成小数。"""

    value = _normalize_cell(value)
    if value is None:
        return None

    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text in {"", "--", "-", "nan%", "inf%", "-inf%", "NaN", "nan"}:
            return None
        if text.endswith("%"):
            text = text[:-1]
            try:
                number = float(text)
            except ValueError:
                return None
            return number / 100
        try:
            number = float(text)
        except ValueError:
            return None
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

    if not math.isfinite(number):
        return None
    # Excel 有时会直接存 0.6008，有时会存 60.08；大于 1 的按百分数处理。
    return number / 100 if abs(number) > 1 else number


def _clean_numeric_value(value: Any) -> Any:
    value = _normalize_cell(value)
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if text in {"", "--", "-", "nan", "NaN"}:
            return None
        try:
            return float(text)
        except ValueError:
            return value.strip()
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _is_percent_column(column: str) -> bool:
    return any(keyword in column for keyword in PERCENT_KEYWORDS)


def _normalize_filter_number(value: Any, column: str) -> float | None:
    value = _clean_numeric_value(value)
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if _is_percent_column(column) and abs(number) > 1:
        return number / 100
    return number


def _apply_structured_filter(data: pd.DataFrame, field: str, condition: Any) -> pd.DataFrame:
    if field not in data.columns or condition in (None, "", [], {}):
        return data

    # 兼容旧版字段值多选：{"店铺名称": ["A", "B"]}
    if isinstance(condition, list):
        if not condition:
            return data
        normalized = {str(value) for value in condition}
        return data[data[field].map(lambda value: "" if value is None else str(value)).isin(normalized)]

    if not isinstance(condition, dict):
        return data

    mode = condition.get("mode")
    series = data[field]

    if mode is None and condition.get("op"):
        op = condition.get("op")
        value = condition.get("value")
        if op == "contains":
            mode = "contains"
            condition = {"keyword": value}
        elif op in {"lt", "lte"}:
            mode = "range"
            condition = {"max": value, "include_max": op == "lte"}
        elif op in {"gt", "gte"}:
            mode = "range"
            condition = {"min": value, "include_min": op == "gte"}
        elif op == "between":
            mode = "range"
            condition = {
                "min": condition.get("min"),
                "max": condition.get("max"),
                "include_min": False,
                "include_max": False,
            }

    if mode == "contains":
        keyword = str(condition.get("keyword") or "").strip()
        if not keyword:
            return data
        return data[series.map(lambda value: "" if value is None else str(value)).str.contains(keyword, case=False, na=False)]

    if mode == "range":
        min_value = condition.get("min")
        max_value = condition.get("max")
        value_type = condition.get("value_type")
        include_min = bool(condition.get("include_min"))
        include_max = bool(condition.get("include_max"))

        if value_type == "date":
            comparable = pd.to_datetime(series, errors="coerce")
            min_bound = pd.to_datetime(min_value, errors="coerce") if min_value not in (None, "") else None
            max_bound = pd.to_datetime(max_value, errors="coerce") if max_value not in (None, "") else None
        else:
            comparable = pd.to_numeric(series, errors="coerce")
            min_bound = _normalize_filter_number(min_value, field)
            max_bound = _normalize_filter_number(max_value, field)

        mask = pd.Series(True, index=data.index)
        if min_bound is not None and not pd.isna(min_bound):
            mask &= comparable >= min_bound if include_min else comparable > min_bound
        if max_bound is not None and not pd.isna(max_bound):
            mask &= comparable <= max_bound if include_max else comparable < max_bound
        return data[mask.fillna(False)]

    return data


def clean_profit_frame(frame: pd.DataFrame, profit_file: ProfitFile) -> pd.DataFrame:
    cleaned = frame.copy()
    cleaned.columns = [str(column).strip() for column in cleaned.columns]
    cleaned = cleaned.dropna(how="all")

    for column in cleaned.columns:
        if _is_percent_column(column):
            cleaned[column] = cleaned[column].map(_clean_percent_value)
        else:
            cleaned[column] = cleaned[column].map(_clean_numeric_value)

    cleaned["平台"] = profit_file.platform
    cleaned["数据日期"] = profit_file.data_date.isoformat()
    cleaned["来源文件"] = profit_file.path.name
    return cleaned


class ProfitDataStore:
    """按平台分开缓存清洗后的利润表，避免每次筛选都重新读 Excel。"""

    def __init__(self, data_dir: Path = DEFAULT_DATA_DIR) -> None:
        self.data_dir = data_dir
        self._frames_by_platform: dict[str, pd.DataFrame] = {}
        self._files: list[ProfitFile] = []
        self._loaded_at: datetime | None = None

    def reload(self) -> None:
        files = list_profit_files(self.data_dir)
        frames_by_platform: dict[str, list[pd.DataFrame]] = {"拼多多": [], "淘宝": []}

        for profit_file in files:
            frame = pd.read_excel(profit_file.path)
            frames_by_platform.setdefault(profit_file.platform, []).append(
                clean_profit_frame(frame, profit_file)
            )

        self._frames_by_platform = {
            platform: pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
            for platform, frames in frames_by_platform.items()
        }
        self._files = files
        self._loaded_at = datetime.now()

    def ensure_loaded(self) -> None:
        if self._loaded_at is None:
            self.reload()

    @property
    def loaded_at(self) -> str | None:
        return self._loaded_at.isoformat(timespec="seconds") if self._loaded_at else None

    def metadata(self, allowed_platforms: set[str] | None = None) -> dict[str, Any]:
        self.ensure_loaded()
        files = [
            item for item in self._files
            if allowed_platforms is None or item.platform in allowed_platforms
        ]
        dates = sorted({item.data_date.isoformat() for item in files})
        platforms = sorted({item.platform for item in files})

        columns_by_platform: dict[str, list[str]] = {}
        for platform, frame in self._frames_by_platform.items():
            if allowed_platforms is not None and platform not in allowed_platforms:
                continue
            columns_by_platform[platform] = list(frame.columns) if not frame.empty else SYSTEM_COLUMNS

        return {
            "data_dir": str(self.data_dir),
            "loaded_at": self.loaded_at,
            "file_count": len(files),
            "platforms": platforms,
            "dates": dates,
            "columns_by_platform": columns_by_platform,
            "files": [
                {"platform": item.platform, "date": item.data_date.isoformat(), "name": item.path.name}
                for item in files
            ],
        }

    def _filtered_frame(
        self,
        platform: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        value_filters: dict[str, Any] | None = None,
        allowed_platforms: set[str] | None = None,
    ) -> pd.DataFrame:
        self.ensure_loaded()

        frames = []
        for current_platform, frame in self._frames_by_platform.items():
            if allowed_platforms is not None and current_platform not in allowed_platforms:
                continue
            if platform and platform != "全部" and current_platform != platform:
                continue
            if not frame.empty:
                frames.append(frame)

        data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if data.empty:
            return data

        if start_date:
            data = data[data["数据日期"] >= start_date.isoformat()]
        if end_date:
            data = data[data["数据日期"] <= end_date.isoformat()]

        for field, condition in (value_filters or {}).items():
            data = _apply_structured_filter(data, field, condition)

        return data

    @staticmethod
    def _numeric(data: pd.DataFrame, column: str) -> pd.Series:
        if column not in data.columns:
            return pd.Series(0, index=data.index, dtype="float64")
        return pd.to_numeric(data[column], errors="coerce").fillna(0)

    @staticmethod
    def _text(data: pd.DataFrame, column: str, default: str) -> pd.Series:
        if column not in data.columns:
            return pd.Series(default, index=data.index, dtype="object")
        return data[column].fillna(default).map(lambda value: default if str(value).strip() == "" else str(value))

    def _dashboard_base_frame(self, data: pd.DataFrame) -> pd.DataFrame:
        base = data.copy()
        base["_person"] = self._text(base, "负责人", "未分配负责人")
        base["_store"] = self._text(base, "店铺名称", "未命名店铺")
        base["_product_code"] = self._text(base, "商品编码", "未编码")
        if "商品名称" in base.columns:
            base["_product_name"] = self._text(base, "商品名称", "未命名商品")
        else:
            base["_product_name"] = self._text(base, "商品标题", "未命名商品")
        base["_date"] = self._text(base, "数据日期", "")
        base["_orders"] = self._numeric(base, "单量")
        if base["_orders"].sum() == 0:
            base["_orders"] = self._numeric(base, "昨日单量")
        base["_revenue"] = self._numeric(base, "订单金额")
        base["_cost"] = self._numeric(base, "货品成本")
        base["_shipping"] = self._numeric(base, "快递成本")
        base["_gross_profit"] = self._numeric(base, "毛利")
        base["_promotion"] = self._numeric(base, "推广费")
        if base["_promotion"].sum() == 0:
            base["_promotion"] = self._numeric(base, "花费")
        base["_platform_profit"] = self._numeric(base, "平台利润")
        base["_refund"] = self._numeric(base, "退款金额")
        base["_service_fee"] = pd.Series(0, index=base.index, dtype="float64")
        for column in base.columns:
            if column.startswith("技术服务费"):
                base["_service_fee"] += pd.to_numeric(base[column], errors="coerce").fillna(0)
        base["_after_sale"] = self._numeric(base, "预估售后")
        base["_insurance"] = self._numeric(base, "运费险")
        base["_tax"] = self._numeric(base, "税费")
        return base

    @staticmethod
    def _ratio(numerator: float, denominator: float) -> float:
        return float(numerator / denominator) if denominator else 0.0

    def _aggregate_records(self, data: pd.DataFrame, group_columns: list[str], rename: dict[str, str]) -> list[dict[str, Any]]:
        if data.empty:
            return []
        grouped = (
            data.groupby(group_columns, dropna=False)
            .agg(
                orders=("_orders", "sum"),
                revenue=("_revenue", "sum"),
                cost=("_cost", "sum"),
                shipping=("_shipping", "sum"),
                gross_profit=("_gross_profit", "sum"),
                promotion=("_promotion", "sum"),
                platform_profit=("_platform_profit", "sum"),
                stores=("_store", "nunique"),
                products=("_product_code", "nunique"),
            )
            .reset_index()
        )

        records = []
        for row in grouped.to_dict(orient="records"):
            revenue = float(row["revenue"] or 0)
            record = {
                **{rename.get(column, column): row[column] for column in group_columns},
                "orders": float(row["orders"] or 0),
                "revenue": revenue,
                "cost": float(row["cost"] or 0),
                "shipping": float(row["shipping"] or 0),
                "gross_profit": float(row["gross_profit"] or 0),
                "gross_margin": self._ratio(float(row["gross_profit"] or 0), revenue),
                "promotion": float(row["promotion"] or 0),
                "promotion_pct": self._ratio(float(row["promotion"] or 0), revenue),
                "platform_profit": float(row["platform_profit"] or 0),
                "profit_rate": self._ratio(float(row["platform_profit"] or 0), revenue),
                "cost_pct": self._ratio(float(row["cost"] or 0), revenue),
                "shipping_pct": self._ratio(float(row["shipping"] or 0), revenue),
                "stores": int(row.get("stores") or 0),
                "products": int(row.get("products") or 0),
            }
            records.append(record)
        return records

    def dashboard(
        self,
        platform: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        value_filters: dict[str, Any] | None = None,
        allowed_platforms: set[str] | None = None,
    ) -> dict[str, Any]:
        data = self._filtered_frame(
            platform=platform,
            start_date=start_date,
            end_date=end_date,
            value_filters=value_filters,
            allowed_platforms=allowed_platforms,
        )
        if data.empty:
            return {
                "kpi": {},
                "daily": [],
                "people": [],
                "stores": [],
                "products": [],
                "cost": {},
                "loaded_at": self.loaded_at,
            }

        base = self._dashboard_base_frame(data)
        revenue = float(base["_revenue"].sum())
        gross_profit = float(base["_gross_profit"].sum())
        promotion = float(base["_promotion"].sum())
        platform_profit = float(base["_platform_profit"].sum())
        cost = float(base["_cost"].sum())
        shipping = float(base["_shipping"].sum())
        service_fee = float(base["_service_fee"].sum())
        after_sale = float(base["_after_sale"].sum())
        insurance = float(base["_insurance"].sum())
        tax = float(base["_tax"].sum())
        other_fee = service_fee + after_sale + insurance + tax

        kpi = {
            "row_count": int(len(base)),
            "orders": float(base["_orders"].sum()),
            "stores": int(base["_store"].nunique()),
            "products": int(base["_product_code"].nunique()),
            "revenue": revenue,
            "gross_profit": gross_profit,
            "gross_margin": self._ratio(gross_profit, revenue),
            "promotion": promotion,
            "promotion_pct": self._ratio(promotion, revenue),
            "platform_profit": platform_profit,
            "profit_rate": self._ratio(platform_profit, revenue),
            "cost": cost,
            "cost_pct": self._ratio(cost, revenue),
            "shipping": shipping,
            "shipping_pct": self._ratio(shipping, revenue),
            "other_fee": other_fee,
        }

        daily = self._aggregate_records(base, ["_date"], {"_date": "date"})
        people = self._aggregate_records(base, ["_person"], {"_person": "person"})
        stores = self._aggregate_records(base, ["_person", "_store"], {"_person": "person", "_store": "store"})
        products = self._aggregate_records(
            base,
            ["_product_code", "_product_name"],
            {"_product_code": "code", "_product_name": "name"},
        )

        return {
            "kpi": kpi,
            "daily": sorted(daily, key=lambda item: item.get("date") or ""),
            "people": sorted(people, key=lambda item: item["revenue"], reverse=True),
            "stores": sorted(stores, key=lambda item: item["revenue"], reverse=True),
            "products": sorted(products, key=lambda item: item["revenue"], reverse=True),
            "cost": {
                "cost": cost,
                "shipping": shipping,
                "promotion": promotion,
                "other_fee": other_fee,
                "platform_profit": platform_profit,
            },
            "loaded_at": self.loaded_at,
        }

    def query(
        self,
        platform: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        fields: list[str] | None = None,
        value_filters: dict[str, Any] | None = None,
        allowed_platforms: set[str] | None = None,
        page: int = 1,
        page_size: int = 100,
    ) -> dict[str, Any]:
        data = self._filtered_frame(
            platform=platform,
            start_date=start_date,
            end_date=end_date,
            value_filters=value_filters,
            allowed_platforms=allowed_platforms,
        )
        if data.empty:
            return self._empty_result(page, page_size)

        all_columns = list(data.columns)
        if fields:
            requested_columns = set(fields)
            selected_columns = [column for column in all_columns if column in requested_columns]
        else:
            selected_columns = all_columns
        if not selected_columns:
            selected_columns = all_columns

        total = len(data)
        start = max(page - 1, 0) * page_size
        end = start + page_size
        page_data = data.iloc[start:end][selected_columns]

        return {
            "rows": self._records(page_data),
            "columns": selected_columns,
            "all_columns": all_columns,
            "summary": self._summary(data),
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "pages": math.ceil(total / page_size) if page_size else 0,
            },
            "loaded_at": self.loaded_at,
        }

    def field_values(
        self,
        field: str,
        platform: str | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        keyword: str | None = None,
        allowed_platforms: set[str] | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        result = self.query(
            platform=platform,
            start_date=start_date,
            end_date=end_date,
            fields=None,
            allowed_platforms=allowed_platforms,
            page=1,
            page_size=1,
        )
        self.ensure_loaded()

        frames = []
        for current_platform, frame in self._frames_by_platform.items():
            if allowed_platforms is not None and current_platform not in allowed_platforms:
                continue
            if platform and platform != "全部" and current_platform != platform:
                continue
            if not frame.empty:
                frames.append(frame)

        data = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if data.empty or field not in data.columns:
            return {"field": field, "values": [], "total": 0, "loaded_at": self.loaded_at}

        if start_date:
            data = data[data["数据日期"] >= start_date.isoformat()]
        if end_date:
            data = data[data["数据日期"] <= end_date.isoformat()]

        values = data[field].map(lambda value: "" if value is None else str(value)).drop_duplicates()
        if keyword:
            values = values[values.str.contains(keyword, case=False, na=False)]

        value_list = sorted(values.tolist(), key=lambda item: (item == "", item))
        return {
            "field": field,
            "values": value_list[:limit],
            "total": len(value_list),
            "loaded_at": self.loaded_at,
            "query_total": result["pagination"]["total"],
        }

    def _empty_result(self, page: int, page_size: int) -> dict[str, Any]:
        return {
            "rows": [],
            "columns": [],
            "all_columns": [],
            "summary": {"row_count": 0, "order_amount": 0, "platform_profit": 0, "avg_profit_rate": None},
            "pagination": {"page": page, "page_size": page_size, "total": 0, "pages": 0},
            "loaded_at": self.loaded_at,
        }

    @staticmethod
    def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        records = frame.to_dict(orient="records")
        return [{key: _normalize_cell(value) for key, value in row.items()} for row in records]

    @staticmethod
    def _summary(frame: pd.DataFrame) -> dict[str, Any]:
        def sum_column(column: str) -> float:
            if column not in frame.columns:
                return 0
            return float(pd.to_numeric(frame[column], errors="coerce").fillna(0).sum())

        profit_rate = None
        if "利润率" in frame.columns:
            series = pd.to_numeric(frame["利润率"], errors="coerce").dropna()
            if not series.empty:
                profit_rate = float(series.mean())

        return {
            "row_count": int(len(frame)),
            "order_amount": sum_column("订单金额"),
            "platform_profit": sum_column("平台利润"),
            "avg_profit_rate": profit_rate,
        }
