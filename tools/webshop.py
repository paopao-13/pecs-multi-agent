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
    "你是 WebShop 购物 Agent。根据页面摘要和购物目标选下一步动作。\n"
    "动作格式：\n"
    "- search[关键词]：搜索商品（仅在搜索页且有未尝试关键词时用）\n"
    "- click[BUTTON_X]：点击选项/商品（仅在搜索结果页选商品时用，X 为编号）\n"
    "- buy：立即购买\n\n"
    "决策规则（严格按顺序判断）：\n"
    "1. 如果页面有 Buy按钮 且商品名包含 goal 中≥2个关键属性 → 必须输出 buy\n"
    "2. 如果是搜索结果页 且有商品匹配 goal 关键属性 → click 对应商品（如 click[BUTTON_1]）\n"
    "3. 如果是搜索页 或 当前关键词已搜过且无结果 → search[新关键词]（用 goal 里的英文同义词）\n"
    "4. 步数将尽（历史≥10步）且还没买 → 必须 buy 避免空手\n"
    "只输出一个动作本身，不要任何解释。"
)


def _summarize_obs(obs: str) -> str:
    """从 HTML 抽取关键购物信息，降低 LLM 解析负担。

    原始 HTML 2000 字符 LLM 难解析，本函数提取页面类型、Buy按钮、商品列表，
    压缩成结构化摘要，让 LLM 聚焦决策。
    """
    if not obs:
        return "[空页面]"
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(obs, "html.parser")
        # 检测页面类型与关键按钮
        has_search_box = bool(soup.find("input", {"name": "search"}) or soup.find("input", {"id": "search"}))
        has_buy_button = "Buy Now" in obs or "buy-now" in obs.lower() or "buy_now" in obs.lower()
        # 抽取商品选项（搜索结果页的 item 或详情页的选项）
        items = []
        for div in soup.select("[class*=product], [class*=item], [class*=SearchItem]")[:5]:
            txt = div.get_text(" ", strip=True)[:100]
            if txt:
                items.append(txt)
        # 抽取可点击按钮编号
        buttons = []
        import re as _re_btn
        for m in _re_btn.finditer(r"BUTTON_(\d+)", obs):
            if m.group(1) not in buttons:
                buttons.append(m.group(1))
        return (
            f"[页面类型] {'商品详情页(可购买)' if has_buy_button else '搜索结果页' if items else '搜索页' if has_search_box else '其他'}\n"
            f"[Buy按钮] {'有-可立即购买' if has_buy_button else '无'}\n"
            f"[可点击按钮] BUTTON_{',BUTTON_'.join(buttons[:8]) if buttons else '无'}\n"
            f"[商品/选项] {items[:3] if items else '空'}"
        )
    except Exception:
        # BeautifulSoup 失败时降级用原始文本前 800 字符
        return obs[:800]


def _decide_webshop_action(goal: str, obs: str, history: list) -> str:
    """让 LLM 根据当前页面与历史，决定下一个 WebShop 动作。

    采用"规则兜底 + LLM 决策"混合策略：
    1. 规则层先判断页面类型与必做动作（避免 LLM 陷入 search 循环）
    2. LLM 层做精细化选择（选哪个商品/关键词）
    """
    from agents.llm_utils import call_llm
    hist = "\n".join(history[-6:])
    obs_summary = _summarize_obs(obs)

    # === 规则兜底层：打破 search 循环 ===
    # 判断页面状态
    has_buy_button = "Buy Now" in obs or "buy-now" in obs.lower()
    has_search_results = "[button]" in obs or "BUTTON_" in obs
    has_search_box = 'name="search"' in obs or 'id="search"' in obs
    search_count = sum(1 for h in history if h.startswith("step") and "search[" in h)
    click_count = sum(1 for h in history if h.startswith("step") and "click[" in h)

    # 规则 1：详情页（有 Buy 按钮）→ 必须买（click[Buy Now] 触发结算，reward>0）
    # 注意：WebShop 的购买动作是 click[Buy Now]，不是 buy（buy 不触发结算）
    if has_buy_button:
        return "click[Buy Now]"

    # 规则 2：搜索结果页（有商品按钮），且已经搜索过 → click 最匹配的商品进详情页
    # 这是打破 search 循环的关键：搜到结果就进去看，不要反复换关键词
    # text_rich 模式下商品按钮格式为 [button] B09RFDP1C3 [button_]，ASIN 即 click 参数
    if has_search_results and search_count >= 1 and click_count < search_count:
        # 从 obs 提取所有可用 ASIN（[button] XXXXX [button_] 格式）
        import re as _re_asin
        asins = _re_asin.findall(r"\[button\]\s*([A-Z0-9]{10})\s*\[button_\]", obs)
        if not asins:
            # 没有商品 ASIN，可能是选项按钮，兜底 click 第一个 [button]
            btns = _re_asin.findall(r"\[button\]\s*([^\[\]]+?)\s*\[button_\]", obs)
            if btns:
                return f"click[{btns[0].strip()}]"
            return "search[" + goal[:30] + "]"
        # 用 LLM 从候选 ASIN 中选最匹配 goal 的商品
        asin_list = "\n".join(f"- {a}" for a in asins[:8])
        prompt = (
            f"购物目标：{goal}\n\n"
            f"当前页面有这些商品（ASIN 编号）：\n{asin_list}\n\n"
            f"页面文本：\n{obs_summary}\n\n"
            f"请选择最匹配购物目标的商品 ASIN。只输出一个动作 click[ASIN]，ASIN 必须从上面列表里选。"
        )
        action, _ = call_llm(prompt, "你是购物助手，选最匹配的商品 ASIN。", role="executor")
        action = (action or "").strip()
        # 校验：click[X] 且 X 是有效 ASIN
        m = _re_asin.match(r"click\[([A-Z0-9]{10})\]", action)
        if m and m.group(1) in asins:
            return action
        # LLM 没选对，兜底 click 第一个 ASIN（宁可进详情页看看，也不要反复 search）
        return f"click[{asins[0]}]"

    # 规则 3：搜索页（无结果），用 LLM 生成关键词
    prompt = (
        f"购物目标：{goal}\n\n"
        f"已执行动作：\n{hist}\n\n"
        f"当前页面摘要：\n{obs_summary}\n\n"
        f"你在搜索页。请根据购物目标生成一个搜索关键词（英文，用商品的英文名称和关键属性）。"
        f"只输出 search[关键词]，不要解释。注意：不要重复已搜过的关键词。"
    )
    action, _ = call_llm(prompt, "你是购物助手，生成搜索关键词。", role="executor")
    action = (action or "").strip()
    if action.startswith("search[") and "]" in action:
        return action
    # 兜底：用 goal 的关键词
    import re as _re_kw
    kws = _re_kw.findall(r"[a-z]+", goal.lower())
    stop = {"find", "me", "a", "an", "the", "with", "under", "for", "and", "of", "to", "in", "on"}
    kws = [k for k in kws if k not in stop][:3]
    return f"search[{' '.join(kws)}]"


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
    # 传 instruction 让 server 端按语义匹配 task，避免随机 goal 与指令不匹配
    goal, obs = env.reset(instruction=instruction)
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
        if done or action.strip().lower() in ("buy", "click[buy now]"):
            break
        # buy 兜底：步数过半且 reward 仍 0，强制购买避免空手（至少拿到部分 reward）
        if i >= env.max_steps - 3 and last_reward == 0.0:
            history.append(f"step{i + 2}: click[Buy Now] (强制结算)")
            obs, reward, done, _info = env.step("click[Buy Now]")
            last_reward = reward
            break

    return (
        f"WebShop 交互完成（共 {len(history)} 步, 奖励={last_reward:.3f}）\n"
        f"最终页面摘要：{obs[:800]}\n"
        f"动作序列：{' -> '.join(history)}"
    )


def _decide_webshop_action_llm(goal: str, obs: str, history: list) -> str:
    """纯 LLM 决策（无规则层），给 ReAct 基线用。

    与 PECS 的 _decide_webshop_action（规则层）对比：
    - PECS: 规则层打破 search 循环（强制 click[ASIN] 进详情页、click[Buy Now] 结算）
    - ReAct: 纯 LLM 自己判断页面类型和该做什么，容易陷入 search 循环

    这是公平对比的核心：PECS 的规则层是 Executor 启发式优化，ReAct 没有这种优化。
    """
    from agents.llm_utils import call_llm
    hist = "\n".join(history[-6:])
    obs_summary = _summarize_obs(obs)
    prompt = (
        f"购物目标：{goal}\n\n"
        f"已执行动作：\n{hist}\n\n"
        f"当前页面摘要：\n{obs_summary}\n\n"
        f"下一个动作（search[关键词] / click[ASIN或按钮名] / click[Buy Now]）："
    )
    action, _ = call_llm(prompt, _WEBSHOP_DECISION_SYSTEM, role="executor")
    return (action or "").strip()


def webshop_interact_react(args: dict) -> str:
    """ReAct 基线的 WebShop 交互（纯 LLM 决策，无规则层）。

    与 PECS 的 webshop_interact 对比：
    - 相同点：都用 _summarize_obs 预处理 obs，都传 instruction 匹配 goal（环境配置）
    - 不同点：用 _decide_webshop_action_llm（纯 LLM）替代 _decide_webshop_action（规则层）
    - 预期：LLM 容易陷入 search 循环，不 click 商品/不 buy，导致 reward=0
    """
    instruction = args.get("instruction", "")
    if not instruction:
        return "错误：缺少 instruction 参数"

    env = get_real_env()
    goal, obs = env.reset(instruction=instruction)
    if not goal:
        goal = instruction

    history: list = []
    last_reward = 0.0
    for i in range(env.max_steps):
        action = _decide_webshop_action_llm(goal, obs, history)
        if not action:
            break
        history.append(f"step{i + 1}: {action}")
        obs, reward, done, _info = env.step(action)
        last_reward = reward
        if done:
            break
        # 步数将尽时强制结算（避免空手，但 ReAct 不强制 click[Buy Now]）
        if i >= env.max_steps - 2 and last_reward == 0.0:
            history.append(f"step{i + 2}: click[Buy Now] (步数将尽兜底)")
            obs, reward, done, _info = env.step("click[Buy Now]")
            last_reward = reward
            break

    return (
        f"WebShop 交互完成（共 {len(history)} 步, 奖励={last_reward:.3f}）\n"
        f"最终页面摘要：{obs[:800]}\n"
        f"动作序列：{' -> '.join(history)}"
    )


def _decide_webshop_action_light(goal: str, obs: str, history: list) -> str:
    """轻量规则层（给 ReAct 公平对比用）：有 Buy 就买，但不强制 click[ASIN]。

    介于 PECS 完整规则层和 ReAct 纯 LLM 之间，用于消融实验：
    - 相同点（购物常识）：详情页有 Buy 按钮 → click[Buy Now]（任何 Agent 都该知道）
    - 不同点（PECS 优势）：不强制 click[ASIN] 进详情页，LLM 自己决定点哪个商品
    - 预期：ReAct-light 仍低于 PECS，证明 PECS 优势来自"打破 search 循环"而非"有规则层"本身
    """
    from agents.llm_utils import call_llm

    # 规则1（购物常识）：详情页有 Buy 按钮 → 必须买（和 PECS 相同的基本购物逻辑）
    # 这一层规则不涉及"打破 search 循环"，只是"会买东西"的基本能力
    has_buy_button = "Buy Now" in obs or "buy-now" in obs.lower()
    if has_buy_button:
        return "click[Buy Now]"

    # LLM 层：自由决策 search[关键词] 或 click[ASIN]（不强制进详情页，这是与 PECS 的关键差异）
    # PECS 的规则2会强制"搜到结果即 click[ASIN]"，这里不做这个强制
    hist = "\n".join(history[-6:])
    obs_summary = _summarize_obs(obs)
    prompt = (
        f"购物目标：{goal}\n\n"
        f"已执行动作：\n{hist}\n\n"
        f"当前页面摘要：\n{obs_summary}\n\n"
        f"下一个动作（search[关键词] / click[ASIN或按钮名] / click[Buy Now]）："
    )
    action, _ = call_llm(prompt, _WEBSHOP_DECISION_SYSTEM, role="executor")
    return (action or "").strip()


def webshop_interact_react_light(args: dict) -> str:
    """ReAct 基线的 WebShop 交互（轻量规则层版）。

    三组对比的中间档：
    - vs webshop_interact_react（纯 LLM）：多了"Buy按钮→click[Buy Now]"购物常识
    - vs PECS webshop_interact（完整规则层）：少了"搜到结果即 click[ASIN]"的循环打破规则
    - 预期：成功率介于 ReAct(0%) 和 PECS(25%) 之间，证明 PECS 核心价值是规则2而非规则1
    """
    instruction = args.get("instruction", "")
    if not instruction:
        return "错误：缺少 instruction 参数"

    env = get_real_env()
    goal, obs = env.reset(instruction=instruction)
    if not goal:
        goal = instruction

    history: list = []
    last_reward = 0.0
    for i in range(env.max_steps):
        action = _decide_webshop_action_light(goal, obs, history)
        if not action:
            break
        history.append(f"step{i + 1}: {action}")
        obs, reward, done, _info = env.step(action)
        last_reward = reward
        if done:
            break
        # 步数将尽时强制结算（和 webshop_interact_react 相同的兜底，三组都有）
        if i >= env.max_steps - 2 and last_reward == 0.0:
            history.append(f"step{i + 2}: click[Buy Now] (步数将尽兜底)")
            obs, reward, done, _info = env.step("click[Buy Now]")
            last_reward = reward
            break

    return (
        f"WebShop 交互完成（共 {len(history)} 步, 奖励={last_reward:.3f}）\n"
        f"最终页面摘要：{obs[:800]}\n"
        f"动作序列：{' -> '.join(history)}"
    )


def parse_webshop_reward(text: str) -> float:
    """从文本中提取 WebShop 奖励分数（0~1）。

    兼容多种表述格式，避免因 ReAct final_answer 的自然语言措辞差异
    导致 reward 解析失败（这是之前 ReAct 被误判 0% 的根因）：
    - "奖励=0.714"（webshop_interact 原始格式）
    - "奖励 0.714" / "奖励得分为0.714" / "奖励得分为 0.714"
    - "reward 0.714" / "reward=0.714" / "score 0.714"
    - "Your score (min 0.0, max 1.0) 0.714..."（WebShop 结算页原文）
    """
    import re as _re
    # 按优先级尝试多种正则
    patterns = [
        r"奖励\s*=\s*([0-9]*\.?[0-9]+)",           # 奖励=0.714
        r"奖励得分为\s*([0-9]*\.?[0-9]+)",          # 奖励得分为0.714
        r"奖励\s+([0-9]*\.?[0-9]+)",                # 奖励 0.714
        r"reward\s*[=:]\s*([0-9]*\.?[0-9]+)",       # reward=0.714
        r"score\s*[=:]\s*([0-9]*\.?[0-9]+)",        # score 0.714
        r"得分\s*([0-9]*\.?[0-9]+)",                # 得分0.714
        r"\(min 0\.0, max 1\.0\)\s*([0-9]*\.?[0-9]+)",  # 结算页 (min 0.0, max 1.0) 0.714
    ]
    for pat in patterns:
        m = _re.search(pat, text, _re.IGNORECASE)
        if m:
            val = float(m.group(1))
            if 0.0 <= val <= 1.0:
                return val
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
