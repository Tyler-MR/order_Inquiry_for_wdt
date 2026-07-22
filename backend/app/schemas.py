from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=3, max_length=128)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: str

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    message: str
    user: UserOut


class PlatformOut(BaseModel):
    id: int
    name: str
    platform_type: str
    focus: str
    metric: str

    model_config = {"from_attributes": True}


class CategoryOut(BaseModel):
    id: int
    name: str
    description: str
    tag: str

    model_config = {"from_attributes": True}


class LeadCreate(BaseModel):
    company: str = Field(min_length=2, max_length=128)
    contact: str = Field(min_length=2, max_length=128)
    platform: str = Field(min_length=2, max_length=32)
    category: str = Field(min_length=2, max_length=64)
    message: str | None = Field(default=None, max_length=1000)


class LeadOut(BaseModel):
    id: int
    company: str
    contact: str
    platform: str
    category: str
    message: str | None
    status: str
    created_at: datetime

    model_config = {"from_attributes": True}


class WdtDashboardFilters(BaseModel):
    """两个 Tableau 仪表板共用的维度筛选器。"""

    brand: list[str] = Field(default_factory=list)
    sku_codes: list[str] = Field(default_factory=list)
    product_names: list[str] = Field(default_factory=list)
    shop_names: list[str] = Field(default_factory=list)
    owner_names: list[str] = Field(default_factory=list)
    date_layers: list[str] = Field(default_factory=list)
    time_truncated: bool = True


class WdtOrderQueryRequest(BaseModel):
    start_time: datetime
    end_time: datetime
    platform_ids: list[str] = Field(default_factory=lambda: ["39"])
    page_size: int = Field(default=100, ge=1, le=100)
    time_type: int = Field(default=4, ge=1, le=5)
    # 留空时由后端读取 total_count，自动翻完该时间窗口的所有分页。
    max_pages: int | None = Field(default=None, ge=1, le=1000)
    include_rows: bool = True
    dashboard_filters: WdtDashboardFilters = Field(default_factory=WdtDashboardFilters)


class WdtOrderQueryResponse(BaseModel):
    columns: list[str]
    rows: list[dict]
    rows_complete: bool = True
    order_count: int
    row_count: int
    api_total_count: int
    expected_count: int
    complete: bool
    incomplete_windows: list[str]
    page_count: int
    source_window_count: int
    platform_ids: list[str]
    start_time: str
    end_time: str
    summary: dict[str, Any]
    daily: list[dict[str, Any]]
    shops: list[dict[str, Any]]
    products: list[dict[str, Any]]
    hourly: list[dict[str, Any]] = Field(default_factory=list)
    hourly_series: list[dict[str, Any]] = Field(default_factory=list)
    shop_comparison: list[dict[str, Any]] = Field(default_factory=list)
    product_comparison: list[dict[str, Any]] = Field(default_factory=list)
    owner_comparison: list[dict[str, Any]] = Field(default_factory=list)
    comparison: dict[str, Any] = Field(default_factory=dict)
    filter_options: dict[str, Any] = Field(default_factory=dict)
    active_filters: dict[str, Any] = Field(default_factory=dict)
    pre_filter_order_count: int | None = None
    last_synced_at: str | None = None
    sync_status: str | None = None
