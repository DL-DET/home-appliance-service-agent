"""用户画像、使用记录和订单数据的统一读取入口。

这些数据目前来自 CSV。把读取逻辑放在服务层，而不是散落在 Agent 工具里，
可以让数据格式、权限校验和测试都保持在一个明确边界内。
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from functools import lru_cache
from pathlib import Path

from utils.config_handler import agent_conf
from utils.path_tool import get_abs_path


class DataSourceError(RuntimeError):
    """外部演示数据缺失或格式不合法。"""


@dataclass(frozen=True)
class UserProfile:
    user_id: str
    display_name: str
    city: str
    household: str
    area: str
    product: str
    email: str


@dataclass(frozen=True)
class UsageRecord:
    user_id: str
    month: str
    feature: str
    efficiency: str
    consumables: str
    comparison: str


@dataclass(frozen=True)
class OrderRecord:
    order_id: str
    user_id: str
    status: str
    logistics: str
    updated_at: str


def _read_csv(relative_path: str) -> list[dict[str, str]]:
    """使用标准 CSV 解析器读取数据，避免手工 split 破坏带引号的字段。"""
    path = Path(get_abs_path(relative_path))
    if not path.is_file():
        raise DataSourceError(f"数据文件不存在：{path}")

    with path.open("r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames:
            raise DataSourceError(f"数据文件没有表头：{path}")
        return [
            {key: (value or "").strip() for key, value in row.items()}
            for row in reader
        ]


@lru_cache(maxsize=1)
def get_user_profiles() -> dict[str, UserProfile]:
    rows = _read_csv(agent_conf["user_profiles_path"])
    profiles: dict[str, UserProfile] = {}
    for row in rows:
        user_id = row["用户ID"]
        if not user_id:
            continue
        profiles[user_id] = UserProfile(
            user_id=user_id,
            display_name=row["用户名称"],
            city=row["城市"],
            household=row["家庭结构"],
            area=row["使用面积"],
            product=row["在用产品"],
            email=row["邮箱"],
        )
    return profiles


def get_user_profile(user_id: str) -> UserProfile | None:
    return get_user_profiles().get(user_id)


@lru_cache(maxsize=1)
def get_usage_records() -> dict[tuple[str, str], UsageRecord]:
    rows = _read_csv(agent_conf["external_data_path"])
    records: dict[tuple[str, str], UsageRecord] = {}
    for row in rows:
        user_id = row["用户ID"]
        month = row["时间"]
        if not user_id or not month:
            continue
        records[(user_id, month)] = UsageRecord(
            user_id=user_id,
            month=month,
            feature=row["特征"],
            efficiency=row["清洁效率"],
            consumables=row["耗材"],
            comparison=row["对比"],
        )
    return records


def get_usage_record(user_id: str, month: str) -> UsageRecord | None:
    return get_usage_records().get((user_id, month))


def get_available_months() -> tuple[str, ...]:
    months = {record.month for record in get_usage_records().values()}
    return tuple(sorted(months, reverse=True))


def format_usage_record(record: UsageRecord) -> str:
    """把结构化记录转成稳定 JSON，作为工具结果交给模型。"""
    return json.dumps(asdict(record), ensure_ascii=False)


def normalize_newlines(value: str) -> str:
    """CSV 字段里可能存的是字面量 \\n，展示前还原成真实换行。"""
    return (value or "").replace("\\n", "\n")


@lru_cache(maxsize=1)
def get_orders() -> dict[str, OrderRecord]:
    rows = _read_csv(agent_conf["orders_path"])
    orders: dict[str, OrderRecord] = {}
    for row in rows:
        order_id = row["订单号"]
        if not order_id:
            continue
        orders[order_id] = OrderRecord(
            order_id=order_id,
            user_id=row["用户ID"],
            status=row["状态"],
            logistics=row["物流进度"],
            updated_at=row["更新时间"],
        )
    return orders


def get_order_for_user(order_id: str, current_user_id: str) -> OrderRecord | None:
    """订单不存在和越权访问都返回 None，避免向外泄露订单是否存在。"""
    order = get_orders().get(order_id.strip())
    if order is None or order.user_id != current_user_id:
        return None
    return order
