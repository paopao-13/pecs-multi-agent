"""
Local WebShop-style product selection tool.

The real AgentBench WebShop task runs in an interactive shopping environment.
This adapter keeps the same core decision surface for local tests: given a user
instruction and a product catalog, select the product that best satisfies hard
constraints such as category, price, rating, and attributes.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List


DEFAULT_CATALOG = [
    {
        "id": "ws_tea_001",
        "name": "Organic Jasmine Green Tea 100 bags",
        "category": "tea",
        "price": 18.99,
        "rating": 4.7,
        "attributes": ["organic", "jasmine", "green tea", "100 bags", "caffeine"],
    },
    {
        "id": "ws_tea_002",
        "name": "Decaf Chamomile Herbal Tea 80 bags",
        "category": "tea",
        "price": 14.50,
        "rating": 4.5,
        "attributes": ["decaf", "chamomile", "herbal", "80 bags"],
    },
    {
        "id": "ws_usb_001",
        "name": "USB-C 65W GaN Charger dual port",
        "category": "electronics",
        "price": 29.99,
        "rating": 4.6,
        "attributes": ["usb-c", "65w", "gan", "dual port", "charger"],
    },
    {
        "id": "ws_usb_002",
        "name": "USB-C 30W Compact Charger single port",
        "category": "electronics",
        "price": 15.99,
        "rating": 4.4,
        "attributes": ["usb-c", "30w", "compact", "charger"],
    },
    {
        "id": "ws_bottle_001",
        "name": "Insulated Stainless Steel Water Bottle 24 oz",
        "category": "kitchen",
        "price": 22.00,
        "rating": 4.8,
        "attributes": ["insulated", "stainless steel", "24 oz", "water bottle"],
    },
    {
        "id": "ws_bottle_002",
        "name": "Plastic Sports Water Bottle 32 oz",
        "category": "sports",
        "price": 11.00,
        "rating": 4.1,
        "attributes": ["plastic", "32 oz", "water bottle"],
    },
    {
        "id": "ws_mouse_001",
        "name": "Silent Wireless Ergonomic Mouse",
        "category": "electronics",
        "price": 24.99,
        "rating": 4.5,
        "attributes": ["silent", "wireless", "ergonomic", "mouse"],
    },
    {
        "id": "ws_mouse_002",
        "name": "Wired Gaming Mouse RGB",
        "category": "electronics",
        "price": 19.99,
        "rating": 4.3,
        "attributes": ["wired", "gaming", "rgb", "mouse"],
    },
]


def webshop_select(args: dict) -> str:
    """
    Select the best catalog item for a shopping instruction.

    Args:
        {
            "instruction": "shopping request",
            "catalog": [optional product dicts],
            "max_price": optional hard price cap
        }
    """
    instruction = args.get("instruction", "")
    if not instruction:
        return "错误：缺少 instruction 参数"

    catalog = args.get("catalog") or DEFAULT_CATALOG
    max_price = args.get("max_price")
    if max_price is None:
        max_price = _extract_price_cap(instruction)

    scored = []
    for item in catalog:
        if max_price is not None and float(item.get("price", 0)) > float(max_price):
            continue
        score = _score_item(instruction, item)
        if score > 0:
            scored.append((score, float(item.get("rating", 0)), -float(item.get("price", 0)), item))

    if not scored:
        return "NO_MATCH: 没有找到满足约束的商品"

    scored.sort(reverse=True)
    best = scored[0][3]
    attrs = ", ".join(best.get("attributes", []))
    return (
        f"SELECTED: {best['id']} | {best['name']}\n"
        f"price={best['price']} rating={best['rating']}\n"
        f"matched_attributes={attrs}"
    )


def _score_item(instruction: str, item: Dict[str, Any]) -> int:
    text = instruction.lower()
    haystack = " ".join([
        str(item.get("name", "")),
        str(item.get("category", "")),
        " ".join(str(a) for a in item.get("attributes", [])),
    ]).lower()

    score = 0
    for token in _tokens(text):
        if token in haystack:
            score += 2 if len(token) > 3 else 1

    # Chinese aliases used in sample tasks.
    aliases = {
        "茶": ["tea"],
        "绿茶": ["green tea"],
        "无咖啡因": ["decaf"],
        "充电器": ["charger"],
        "鼠标": ["mouse"],
        "无线": ["wireless"],
        "静音": ["silent"],
        "水杯": ["water bottle"],
        "保温": ["insulated"],
        "不锈钢": ["stainless steel"],
    }
    for zh, terms in aliases.items():
        if zh in instruction:
            score += sum(3 for term in terms if term in haystack)

    return score


def _tokens(text: str) -> Iterable[str]:
    return re.findall(r"[a-z0-9][a-z0-9\-]*", text.lower())


def _extract_price_cap(text: str):
    patterns = [
        r"(?:under|below|less than|<=?)\s*\$?\s*(\d+(?:\.\d+)?)",
        r"\$?\s*(\d+(?:\.\d+)?)\s*(?:or less|以内|以下)",
        r"预算\s*(\d+(?:\.\d+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return float(match.group(1))
    return None
