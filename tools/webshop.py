"""
Local WebShop-style product selection tool.

The real AgentBench WebShop task runs in an interactive shopping environment.
This adapter keeps the same core decision surface for local tests: given a user
instruction and a product catalog, select the product that best satisfies hard
constraints such as category, price, rating, and attributes.
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Iterable, List


# ===========================================================================
# 真实 AgentBench WebShop 支持（Route C：Docker 部署）
# ---------------------------------------------------------------------------
# 当环境变量 WEBSHOP_SERVER_URL 被设置时，webshop_select 会自动改走真实环境
# 的多轮交互（webshop_interact），否则使用本地 8 商品 mock 适配器。
# 这样切换"mock / 真环境"只需一个环境变量，不改动 Planner/Executor 任何代码。
# ===========================================================================

def use_real_env() -> bool:
    """是否启用真实 WebShop 环境（由 WEBSHOP_SERVER_URL 控制）。"""
    return bool(os.environ.get("WEBSHOP_SERVER_URL"))


_REAL_ENV = None


def get_real_env():
    """懒加载单例 WebShopEnv（避免导入期连 Docker）。"""
    global _REAL_ENV
    if _REAL_ENV is None:
        from tools.webshop_env import WebShopEnv
        _REAL_ENV = WebShopEnv()
    return _REAL_ENV


# LLM 决策购物动作的提示词（用于多轮交互中"看页面→选下一步"）
_WEBSHOP_DECISION_SYSTEM = (
    "你是一个 WebShop 购物 Agent。根据当前页面（observation）和购物目标（goal），"
    "选择下一个动作。动作必须是以下之一：\n"
    "- search[关键词]：搜索商品（关键词用英文）\n"
    "- click[BUTTON_X]：点击页面上的按钮/选项（X 为编号，如 BUTTON_1）\n"
    "- buy：完成购买（确定已选好符合全部约束的商品后）\n"
    "只输出一个动作本身，不要任何解释或多余文字。"
)


def _decide_webshop_action(goal: str, obs: str, history: list) -> str:
    """让 LLM 根据当前页面与历史，决定下一个 WebShop 动作。"""
    from agents.llm_utils import call_llm
    hist = "\n".join(history[-6:])
    prompt = (
        f"购物目标：{goal}\n\n"
        f"已执行动作：\n{hist}\n\n"
        f"当前页面：\n{obs[:2000]}\n\n"
        f"下一个动作："
    )
    action, _ = call_llm(prompt, _WEBSHOP_DECISION_SYSTEM, role="executor", max_tokens=40)
    return (action or "").strip()


def webshop_interact(args: dict) -> str:
    """
    多轮驱动真实 WebShop 环境，直到 buy/done 或达到步数上限。

    返回一段包含步数、奖励、动作序列与最终页面摘要的文本，供 Synthesizer
    抽取最终选择，也供评测脚本用 parse_webshop_reward 提取得分。
    """
    instruction = args.get("instruction", "")
    if not instruction:
        return "错误：缺少 instruction 参数"

    env = get_real_env()
    goal, obs = env.reset()
    if not goal:
        goal = instruction  # reset 未返回 goal 时（取决于版本）用 instruction 兜底

    history: list = []
    last_reward = 0.0
    for i in range(env.max_steps):
        action = _decide_webshop_action(goal, obs, history)
        if not action:
            break
        history.append(f"step{i + 1}: {action}")
        obs, reward, done, _info = env.step(action)
        last_reward = reward
        if done or action.strip().lower() == "buy":
            break

    return (
        f"WebShop 交互完成（共 {len(history)} 步, 奖励={last_reward:.3f}）\n"
        f"最终页面摘要：{obs[:800]}\n"
        f"动作序列：{' -> '.join(history)}"
    )


def parse_webshop_reward(text: str) -> float:
    """从 webshop_interact 的返回文本中提取奖励分数（0~1）。"""
    import re as _re
    m = _re.search(r"奖励\s*=\s*([0-9]*\.?[0-9]+)", text)
    if m:
        return float(m.group(1))
    return 0.0


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

    # 真实 AgentBench 环境模式：委托给多轮交互驱动
    if use_real_env():
        return webshop_interact(args)

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
