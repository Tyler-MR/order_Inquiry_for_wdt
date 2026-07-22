"""Import the Windows product master snapshot into MySQL."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from sqlalchemy import select

from app.database import SessionLocal
from app.models import ProductMaster


def main(snapshot_path: str) -> None:
    path = Path(snapshot_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise ValueError("商品基础数据快照为空，拒绝覆盖数据库")

    normalized: dict[str, dict[str, str]] = {}
    for raw in items:
        if not isinstance(raw, dict):
            continue
        lookup_code = str(raw.get("lookup_code") or "").strip()
        if not lookup_code:
            continue
        normalized[lookup_code] = {
            "product_code": str(raw.get("product_code") or "").strip(),
            "sku_code": str(raw.get("sku_code") or "").strip(),
            "product_name": str(raw.get("product_name") or "").strip(),
            "product_spec": str(raw.get("product_spec") or "").strip(),
            "source_sheet": str(raw.get("source_sheet") or "").strip(),
            "source_file": str(raw.get("source_file") or "").strip(),
        }
    if not normalized:
        raise ValueError("商品基础数据快照没有有效编码，拒绝覆盖数据库")

    with SessionLocal() as db:
        existing = {
            item.lookup_code: item
            for item in db.scalars(select(ProductMaster)).all()
        }
        for lookup_code, values in normalized.items():
            item = existing.get(lookup_code)
            if item is None:
                item = ProductMaster(lookup_code=lookup_code)
                db.add(item)
            for field, value in values.items():
                setattr(item, field, value)
            item.is_active = True

        for lookup_code, item in existing.items():
            if lookup_code not in normalized:
                item.is_active = False
        db.commit()

    print(f"商品基础数据导入完成：{len(normalized)} 条")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("用法：python scripts/import_product_master.py /app/sync/product_master.json")
    main(sys.argv[1])
