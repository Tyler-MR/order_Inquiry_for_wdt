"""Import a complete shop-owner mapping snapshot into MySQL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from app.database import SessionLocal  # noqa: E402
from app.models import ShopOwnerMap  # noqa: E402
from app.wdt_client import normalize_shop_name  # noqa: E402


def load_rows(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_items: Any = payload.get("items") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        raise ValueError("同步文件必须包含 items 数组")

    rows: dict[str, dict[str, str]] = {}
    for index, item in enumerate(raw_items, start=2):
        if not isinstance(item, dict):
            raise ValueError(f"第 {index} 行不是对象")
        shop_name = normalize_shop_name(item.get("shop_name"))
        owner_name = str(item.get("owner_name") or "").strip()
        platform = str(item.get("platform") or "").strip()
        if not shop_name or not owner_name:
            raise ValueError(f"第 {index} 行缺少店铺名称或负责人")
        # 与 pandas.drop_duplicates(subset=["店铺名称"], keep="last") 一致：
        # 同一归一化店铺重复出现时，以 Excel 最后一次出现的负责人为准。
        rows[shop_name] = {
            "shop_name": shop_name,
            "owner_name": owner_name,
            "platform": platform,
        }

    if not rows:
        raise ValueError("同步文件没有有效的店铺负责人数据")
    return list(rows.values())


def import_rows(rows: list[dict[str, str]]) -> dict[str, int]:
    incoming_names = {row["shop_name"] for row in rows}
    with SessionLocal() as db:
        existing = {
            item.shop_name: item
            for item in db.scalars(select(ShopOwnerMap)).all()
        }
        created = 0
        updated = 0
        for row in rows:
            item = existing.get(row["shop_name"])
            if item is None:
                db.add(ShopOwnerMap(**row, is_active=True))
                created += 1
                continue
            if (
                item.owner_name != row["owner_name"]
                or item.platform != row["platform"]
                or not item.is_active
            ):
                item.owner_name = row["owner_name"]
                item.platform = row["platform"]
                item.is_active = True
                updated += 1

        removed = 0
        for shop_name, item in existing.items():
            if shop_name not in incoming_names:
                db.delete(item)
                removed += 1

        db.commit()
    return {"total": len(rows), "created": created, "updated": updated, "removed": removed}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import shop-owner mapping JSON into MySQL")
    parser.add_argument("input", type=Path, help="JSON snapshot path")
    args = parser.parse_args()
    result = import_rows(load_rows(args.input))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
