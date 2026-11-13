"""Local, user-controlled shelves; independent of automated topic metadata."""
import unicodedata

from .providers import TaskError

COLLECTION_GROUP_LABELS = {
    "ai_tutorial": "AI工具与教程",
    "aigc_tutorial": "AIGC教程",
    "aigc_example": "AIGC成片",
    "entertainment": "娱乐",
    "review": "待确认",
    "other": "其他",
}
COLLECTION_GROUPS = set(COLLECTION_GROUP_LABELS)


def normalize_collection_group(value):
    if not isinstance(value, str) or len(value) > 100:
        raise TaskError("invalid_input", "收藏分类请填写1—40个字")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise TaskError("invalid_input", "收藏分类不能包含换行或隐藏字符")
    value = value.strip()
    value = next((key for key, label in COLLECTION_GROUP_LABELS.items() if value == label), value)
    if len(value) > 40 or value in {"全部", "未确认分类"}:
        raise TaskError("invalid_input", "收藏分类请填写1—40个字，且不能使用筛选名称")
    return value


def collection_group_label(group):
    return COLLECTION_GROUP_LABELS.get(group, group)
