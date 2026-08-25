from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..errors import UnsupportedCalculationError
from .models import CategoryBucket, PlanEvent


ALL_CATEGORIES_ID = "00000000-0000-0000-0000-000000000000"
UNCATEGORIZED_CATEGORY_ID = "uncategorized"


def category_bucket(
    category_id: str | None,
    categories: Mapping[str, Mapping[str, Any]],
) -> CategoryBucket:
    if category_id is None or category_id == UNCATEGORIZED_CATEGORY_ID:
        return CategoryBucket(UNCATEGORIZED_CATEGORY_ID, "Uncategorized", None)
    if category_id == ALL_CATEGORIES_ID:
        return CategoryBucket(ALL_CATEGORIES_ID, "ALL (aggregate)", None)

    category = categories.get(category_id)
    if category is None:
        raise UnsupportedCalculationError(
            "A Plans row references a category missing from the synced category tree",
            {"reason": "unknown_category", "category_id": category_id},
        )
    return CategoryBucket(
        category_id=category_id,
        name=str(category.get("title") or category_id),
        parent_id=category.get("parent"),
    )


def event_category_buckets(
    event: PlanEvent,
    categories: Mapping[str, Mapping[str, Any]],
) -> tuple[CategoryBucket, ...]:
    if not event.category_ids:
        return (category_bucket(None, categories),)
    return tuple(
        category_bucket(category_id, categories) for category_id in event.category_ids
    )
