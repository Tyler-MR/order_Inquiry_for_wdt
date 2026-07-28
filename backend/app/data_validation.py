"""Hourly validation for missing WDT order data and email alerts."""

import asyncio
import logging
import os
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.database import SessionLocal
from app.models import WdtOrder


logger = logging.getLogger(__name__)
LOCAL_TZ = ZoneInfo(os.getenv("APP_TIMEZONE", "Asia/Shanghai"))


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def previous_hour_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """Return the previous complete local-time hour as naive DB datetimes."""

    current = now or datetime.now(LOCAL_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=LOCAL_TZ)
    current_hour = current.astimezone(LOCAL_TZ).replace(
        minute=0,
        second=0,
        microsecond=0,
        tzinfo=None,
    )
    return current_hour - timedelta(hours=1), current_hour


def _time_column(time_type: int):
    columns = {
        1: WdtOrder.modified_at,
        2: WdtOrder.trade_at,
        3: WdtOrder.order_created_at,
        4: func.coalesce(WdtOrder.pay_at, WdtOrder.trade_at),
        5: func.coalesce(WdtOrder.consign_at, WdtOrder.pay_at, WdtOrder.trade_at),
    }
    return columns.get(time_type, columns[4])


def _platform_ids() -> list[str]:
    value = os.getenv("WDT_DATA_CHECK_PLATFORM_IDS", "")
    return [item.strip() for item in value.split(",") if item.strip()]


def count_previous_hour_orders(
    *,
    start_time: datetime,
    end_time: datetime,
    time_type: int,
) -> int:
    """Count local MySQL orders in [start_time, end_time)."""

    statement = select(func.count(WdtOrder.id)).where(
        _time_column(time_type) >= start_time,
        _time_column(time_type) < end_time,
    )
    platform_ids = _platform_ids()
    if platform_ids:
        statement = statement.where(WdtOrder.platform_id.in_(platform_ids))

    with SessionLocal() as db:
        return int(db.scalar(statement) or 0)


def _send_missing_data_email(
    *,
    start_time: datetime,
    end_time: datetime,
    order_count: int,
    checked_at: datetime,
) -> None:
    smtp_username = os.getenv("WDT_SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("WDT_SMTP_PASSWORD", "")
    smtp_host = os.getenv("WDT_SMTP_HOST", "smtp.qq.com").strip()
    smtp_port = int(os.getenv("WDT_SMTP_PORT", "465"))
    smtp_use_ssl = _env_bool("WDT_SMTP_USE_SSL", True)
    sender = os.getenv("WDT_ALERT_EMAIL_FROM", smtp_username).strip()
    recipient = os.getenv("WDT_ALERT_EMAIL_TO", "813173214@qq.com").strip()

    if not smtp_username or not smtp_password or not sender or not recipient:
        raise RuntimeError(
            "缺少邮件配置：请设置 WDT_SMTP_USERNAME、WDT_SMTP_PASSWORD、"
            "WDT_ALERT_EMAIL_FROM 和 WDT_ALERT_EMAIL_TO"
        )

    time_type = int(os.getenv("WDT_DATA_CHECK_TIME_TYPE", "4"))
    time_label = {
        1: "最后修改时间 modified",
        2: "下单时间 trade_time",
        3: "创建时间 created",
        4: "付款时间 pay_time",
        5: "发货时间 consign_time",
    }.get(time_type, "付款时间 pay_time")
    timezone_name = os.getenv("APP_TIMEZONE", "Asia/Shanghai")
    subject = f"旺店通订单数据缺失告警：{start_time:%Y-%m-%d %H}:00"
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "旺店通订单数据验证发现上一小时没有数据。\n\n"
        f"验证时间：{checked_at:%Y-%m-%d %H:%M:%S} ({timezone_name})\n"
        f"检查窗口：{start_time:%Y-%m-%d %H:%M:%S} 至 {end_time:%Y-%m-%d %H:%M:%S}\n"
        f"验证字段：{time_label}\n"
        f"订单数量：{order_count}\n"
        "请检查旺店通接口、同步任务和 Linux 后端服务。"
    )

    if smtp_use_ssl:
        with smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30) as server:
            server.login(smtp_username, smtp_password)
            server.send_message(message)
        return

    tls_context = ssl.create_default_context()
    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as server:
        server.ehlo()
        server.starttls(context=tls_context)
        server.ehlo()
        server.login(smtp_username, smtp_password)
        server.send_message(message)


def validate_previous_hour_once(now: datetime | None = None) -> dict[str, Any]:
    """Validate one completed hour and send an alert only when it is empty."""

    start_time, end_time = previous_hour_window(now)
    time_type = int(os.getenv("WDT_DATA_CHECK_TIME_TYPE", "4"))
    order_count = count_previous_hour_orders(
        start_time=start_time,
        end_time=end_time,
        time_type=time_type,
    )
    result: dict[str, Any] = {
        "status": "ok" if order_count else "missing",
        "start_time": start_time.isoformat(sep=" "),
        "end_time": end_time.isoformat(sep=" "),
        "order_count": order_count,
        "email_sent": False,
    }
    if order_count:
        logger.info(
            "Hourly WDT data validation passed: %s - %s, order_count=%s",
            start_time,
            end_time,
            order_count,
        )
        return result

    try:
        _send_missing_data_email(
            start_time=start_time,
            end_time=end_time,
            order_count=order_count,
            checked_at=datetime.now(LOCAL_TZ).replace(tzinfo=None),
        )
        result["email_sent"] = True
        logger.error(
            "Hourly WDT data validation found no orders and sent an alert: %s - %s",
            start_time,
            end_time,
        )
    except Exception:
        logger.exception(
            "Hourly WDT data validation found no orders, but the alert email failed: %s - %s",
            start_time,
            end_time,
        )
    return result


async def data_validation_loop() -> None:
    """Run at minute 10 of every hour against the previous complete hour."""

    if not _env_bool("WDT_DATA_CHECK_ENABLED", True):
        logger.info("WDT_DATA_CHECK_ENABLED=false，跳过每小时订单数据验证")
        return

    while True:
        now = datetime.now(LOCAL_TZ)
        next_run = now.replace(minute=10, second=0, microsecond=0)
        if next_run <= now:
            next_run += timedelta(hours=1)
        await asyncio.sleep(max(1.0, (next_run - now).total_seconds()))
        try:
            await asyncio.to_thread(validate_previous_hour_once)
        except Exception:
            logger.exception("每小时订单数据验证执行失败，下一小时继续重试")

