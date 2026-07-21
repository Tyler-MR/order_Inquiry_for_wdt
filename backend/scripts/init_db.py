from pathlib import Path
import sys

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.append(str(ROOT_DIR))

from sqlalchemy import select

from app.database import Base, SessionLocal, engine
from app.models import Category, Platform, ShopOwnerMap, User, WdtOrder, WdtSyncRun

PLATFORMS = [
    {"name": "抖音", "platform_type": "内容电商", "focus": "短视频种草、直播转化、爆品测款", "metric": "达人分销"},
    {"name": "快手", "platform_type": "信任电商", "focus": "老铁私域、主播矩阵、复购运营", "metric": "直播供货"},
    {"name": "视频号", "platform_type": "私域增长", "focus": "企微承接、社群复购、本地生活", "metric": "私域成交"},
    {"name": "淘宝", "platform_type": "货架电商", "focus": "搜索承接、品牌旗舰、活动大促", "metric": "品牌阵地"},
    {"name": "拼多多", "platform_type": "性价比渠道", "focus": "组合装、工厂直供、价格带测试", "metric": "规模出单"},
    {"name": "1688", "platform_type": "B2B 批发", "focus": "源头工厂、批采报价、经销代理", "metric": "渠道招商"},
]

CATEGORIES = [
    {"name": "衣物清洁", "description": "洗衣液、洗衣凝珠、柔顺剂、除菌液", "tag": "高复购"},
    {"name": "厨卫清洁", "description": "油污净、管道疏通、洁厕液、除垢剂", "tag": "强场景"},
    {"name": "家居护理", "description": "地板清洁、玻璃清洁、空气清新、消臭喷雾", "tag": "多规格"},
    {"name": "纸品耗材", "description": "抽纸、湿巾、厨房纸、一次性清洁用品", "tag": "走量款"},
]


def seed_if_missing() -> None:
    """创建基础数据；已存在的数据不会重复插入。"""

    with SessionLocal() as db:
        if db.scalar(select(User).where(User.username == "demo_admin")) is None:
            db.add(
                User(
                    username="demo_admin",
                    password="123456",
                    display_name="运营管理员",
                    role="admin",
                )
            )

        for item in PLATFORMS:
            exists = db.scalar(select(Platform).where(Platform.name == item["name"]))
            if exists is None:
                db.add(Platform(**item))

        for item in CATEGORIES:
            exists = db.scalar(select(Category).where(Category.name == item["name"]))
            if exists is None:
                db.add(Category(**item))

        db.commit()


def main() -> None:
    Base.metadata.create_all(bind=engine)
    seed_if_missing()
    print("数据库表和演示数据初始化完成")


if __name__ == "__main__":
    main()
