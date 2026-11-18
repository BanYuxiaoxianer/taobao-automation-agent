"""编排层（深加工版 / by 31）

三件事：
1. 路由：先用规则（关键词 + 评分）判断意图，拿不准才请模型，省 token
2. 会话记忆：最近 N 条原话 + 一份滚动摘要，超长自动压缩
3. 工作流：选品 → 上架 → 合规 → 客服话术 → 运营建议，一步一停还能接着跑
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

import store
from agents import AGENT_CLASSES, build_agents
from config import AGENT_CHAIN, CACHE_CAPACITY, CACHE_ENABLED, CACHE_TTL, PLATFORMS
from core.agent import Agent, AgentReply
from llm import simple_prompt

logger = logging.getLogger(__name__)


# ─────────────────────────── 简易 TTL 缓存 ───────────────────────────

class TTLCache:
    def __init__(self, capacity: int = 200, ttl_seconds: int = 3600):
        self.capacity = capacity
        self.ttl = ttl_seconds
        self._data: dict[str, tuple[float, object]] = {}

    def get(self, key: str):
        item = self._data.get(key)
        if not item:
            return None
        ts, value = item
        if datetime.now().timestamp() - ts >= self.ttl:
            self._data.pop(key, None)
            return None
        return value

    def set(self, key: str, value) -> None:
        if len(self._data) >= self.capacity:
            oldest = min(self._data, key=lambda k: self._data[k][0])
            self._data.pop(oldest, None)
        self._data[key] = (datetime.now().timestamp(), value)

    def clear(self) -> None:
        self._data.clear()


# ─────────────────────────── 意图路由 ───────────────────────────

ROUTE_KEYWORDS: dict[str, list[tuple[str, float]]] = {
    "selector": [("选品", 0.9), ("爆款", 0.9), ("卖什么", 0.9), ("什么好卖", 0.9),
                 ("趋势", 0.7), ("值得做", 0.8), ("蓝海", 0.9), ("品类", 0.5)],
    "lister": [("标题", 0.9), ("上架", 0.9), ("详情", 0.8), ("描述", 0.8), ("卖点", 0.8),
               ("文案", 0.8), ("主图", 0.7), ("seo", 0.8), ("发布", 0.7)],
    "service": [("客服", 0.9), ("买家说", 0.9), ("顾客", 0.8), ("退货", 0.9), ("退款", 0.9),
                ("投诉", 0.9), ("物流", 0.8), ("什么时候发货", 0.9), ("售后", 0.9), ("差评", 0.9)],
    "analyst": [("利润", 0.8), ("毛利", 0.9), ("定价", 0.8), ("定价怎么", 0.9), ("运营", 0.7),
                ("分析", 0.6), ("数据", 0.6), ("转化", 0.8), ("促销", 0.8), ("成本", 0.6)],
    "compliance": [("违规", 0.9), ("广告法", 0.9), ("能写吗", 0.8), ("禁用词", 0.9),
                   ("审核", 0.8), ("文案合规", 0.9)],
}


def rule_route(text: str) -> tuple[str, float]:
    """加权平均打分，返回 (意图, 置信度)。置信度低于 0.6 时交给模型判断。"""
    body = (text or "").lower()
    best, best_score = "chat", 0.0
    for route, keywords in ROUTE_KEYWORDS.items():
        hits = [w for kw, w in keywords if kw in body]
        if not hits:
            continue
        score = sum(hits) / len(hits)
        if score > best_score:
            best, best_score = route, score
    return best, min(best_score, 1.0)


_ROUTE_SYSTEM = """你是电商助手的意图分类器，只输出一个英文标签，不要解释。
可选标签：
- selector 选品、品类评估
- lister 写标题/详情/上架素材
- service 回复买家、售后话术
- analyst 利润、定价、运营数据分析
- compliance 文案合规、违禁词
- chat 其他"""


def llm_route(text: str) -> str:
    raw = simple_prompt(_ROUTE_SYSTEM, text, temperature=0.0, agent="fast").strip().lower()
    for label in ("selector", "lister", "service", "analyst", "compliance", "chat"):
        if label in raw:
            return label
    return "chat"


# ─────────────────────────── 会话记忆 ───────────────────────────

@dataclass
class SessionState:
    session_id: str
    summary: str = ""
    key_info: str = ""
    turns: int = 0

    @classmethod
    def load(cls, session_id: str) -> "SessionState":
        data = store.load_summary(session_id)
        return cls(session_id, data.get("summary", ""), data.get("key_info", ""))

    def context_prompt(self) -> str:
        parts = []
        if self.summary:
            parts.append(f"[历史摘要]\n{self.summary}")
        if self.key_info:
            parts.append(f"[关键信息]\n{self.key_info}")
        return "\n".join(parts)


SUMMARY_TEMPLATE = """把下面这段电商运营对话压缩成中文摘要，要求：
1. 4-6 条要点，覆盖：做什么品类/商品、已确定的价格与成本、已经生成的素材要点、还没解决的问题
2. 原样保留数字、商品名、平台名、链接
3. 不写解释、不加标题

对话：
{conversation}"""


def _offline_advice(text: str) -> str:
    """没模型时给「自由提问」一个体面的回答：告诉用户该去哪个 Agent。"""
    return "\n".join([
        "## 当前处于离线规则模式",
        "",
        f"你问的是：{text}",
        "",
        "这类开放问题需要语言模型才能回答，但下面这些**不需要模型**的功能现在就能用：",
        "",
        "| 要做的事 | 怎么用 |",
        "| --- | --- |",
        "| 写标题/卖点 | 选「上架师」或 `python cli.py lister \"商品名\"` |",
        "| 回买家消息 | 选「客服官」（查你自己的 FAQ 知识库） |",
        "| 查违禁词 | 选「合规审核员」，粘上文案即可 |",
        "| 算利润定价 | 选「运营参谋」 |",
                "",
        "想启用 AI 生成：在项目根目录的 `.env` 里填 `DEEPSEEK_API_KEY=...` 再重启。",
    ])


# ─────────────────────────── 编排器 ───────────────────────────

class Orchestrator:
    def __init__(self):
        self.agents: dict[str, Agent] = build_agents()
        self.cache = TTLCache(CACHE_CAPACITY, CACHE_TTL) if CACHE_ENABLED else None

    # ── 查询 ──
    def list_agents(self) -> list[dict]:
        return [a.describe() for a in self.agents.values()]

    def get(self, key: str) -> Agent | None:
        return self.agents.get(key)

    # ── 单 Agent 调用 ──
    def run_agent(self, key: str, user_input: str, *, session_id: str = "",
                  context: str = "", monitor=store) -> AgentReply:
        agent = self.agents.get(key)
        if agent is None:
            return AgentReply(key, f"[错误] 没有这个 Agent：{key}")
        state = SessionState.load(session_id) if session_id else SessionState("")
        merged_context = "\n".join(p for p in (context, state.context_prompt()) if p)
        history = store.recent_messages(session_id, 6) if session_id else []
        reply = agent.run(user_input, context=merged_context, session_id=session_id,
                          history=history, monitor=monitor)
        if session_id:
            store.append_message(session_id, "user", user_input)
            store.append_message(session_id, "assistant", reply.text)
            self._maybe_summarize(session_id)
        return reply

    # ── 智能对话 ──
    def chat(self, user_input: str, *, session_id: str | None = None,
             force_route: str = "", monitor=store) -> dict:
        session_id = session_id or f"chat_{uuid.uuid4().hex[:8]}"
        if self.cache:
            key = f"chat:{session_id}:{user_input}:{self._turn_count(session_id)}"
            hit = self.cache.get(key)
            if hit is not None:
                return hit

        route, confidence = rule_route(user_input)
        # 规则只对「没有强制指定 + 拿不准」两种情况让位给模型；
        # 离线时 llm_route 返回 "chat"，等于保留规则结果，不额外花一次调用
        if not force_route and route != "chat" and confidence < 0.6:
            llm_pick = llm_route(user_input)
            if llm_pick != "chat":
                logger.info("规则拿不准（%s %.2f），模型判为 %s", route, confidence, llm_pick)
                route = llm_pick
        elif not force_route and route == "chat":
            llm_pick = llm_route(user_input)
            if llm_pick != "chat":
                logger.info("规则无命中，模型判为 %s", llm_pick)
                route = llm_pick
        if force_route:
            route = force_route

        if route == "chat":
            system = ("你是电商运营顾问，回答简洁具体。不确定的信息直说不确定，"
                      "不要编造平台数据。涉及金额、价格、链接时原样保留。")
            answer = simple_prompt(system, user_input, agent="fast")
            reply = AgentReply("顾问", answer)
            if not reply.ok:
                # 没模型时别把错误直接甩给用户，给一份能干活的分诊建议
                reply = AgentReply("顾问", _offline_advice(user_input), fallback=True)
            if session_id:
                store.append_message(session_id, "user", user_input)
                store.append_message(session_id, "assistant", reply.text)
        else:
            reply = self.run_agent(route, user_input, session_id=session_id, monitor=monitor)

        payload = {
            "session_id": session_id,
            "route": route,
            "route_label": self.agents[route].name if route in self.agents else "顾问",
            "text": reply.text,
            "fallback": reply.fallback,
            "trail": reply.trail(),
            "steps": [s.to_dict() for s in reply.steps],
        }
        if self.cache:
            self.cache.set(key, payload)
        return payload

    def _turn_count(self, session_id: str) -> int:
        try:
            return len(store.recent_messages(session_id, 50))
        except Exception:  # noqa: BLE001
            return 0

    def _maybe_summarize(self, session_id: str, threshold: int = 16) -> None:
        """超过阈值就把老对话压成摘要，防止上下文无限膨胀"""
        try:
            msgs = store.recent_messages(session_id, threshold + 8)
        except Exception:  # noqa: BLE001
            return
        if len(msgs) < threshold:
            return
        transcript = "\n".join(f"{m['role']}: {m['content']}" for m in msgs[:threshold])
        summary = simple_prompt("你是对话摘要器，只输出摘要正文。",
                                SUMMARY_TEMPLATE.format(conversation=transcript),
                                temperature=0.2, agent="fast")
        if summary and not summary.startswith("[错误]"):
            info = "；".join(re.findall(r"[^\s，。]{2,12}", summary)[:6])
            store.save_summary(session_id, summary[:1200], info)
            logger.info("会话 %s 已压缩为摘要（%d 字）", session_id[:12], len(summary))

    # ── 一键工作流 ──
    def run_workflow(self, category: str, *, budget: str = "", audience: str = "",
                     product: str = "", platform: str = "taobao",
                     session_id: str | None = None, monitor=store) -> dict:
        session_id = session_id or f"workflow_{uuid.uuid4().hex[:8]}"
        plat = PLATFORMS.get(platform, PLATFORMS["taobao"])
        results: dict[str, object] = {"category": category, "platform": plat["label"], "steps": []}

        def step(agent_key: str, prompt: str, label: str) -> AgentReply:
            context = (f"品类：{category}\n预算：{budget or '未填'}\n目标人群：{audience or '未填'}\n"
                       f"目标平台：{plat['label']}（标题上限 {plat['title_max']} 字）")
            reply = self.run_agent(agent_key, prompt, session_id=session_id,
                                   context=context, monitor=monitor)
            results["steps"].append({"agent": agent_key, "label": label,
                                     "text": reply.text, "fallback": reply.fallback})
            return reply

        step("selector", f"请评估「{category}」这个品类现在值不值得做。", "1. 选品评估")
        step("lister", f"请为「{product or category}」写一套可以直接上架到{plat['label']}的素材。", "2. 上架素材")
        step("compliance", f"请检查上架素材里是否有广告法风险：{category} / {product or category}", "3. 合规审核")
        step("service", f"请给出「{product or category}」最常见的 5 个买家问题及回复。", "4. 客服话术")
        step("analyst", f"请针对「{category}」新店起步给出定价与运营建议。", "5. 运营建议")

        results["session_id"] = session_id
        results["markdown"] = self._workflow_markdown(results)
        return results

    @staticmethod
    def _workflow_markdown(results: dict) -> str:
        lines = [f"# 一键工作流报告：{results['category']}（{results['platform']}）", ""]
        for item in results["steps"]:
            badge = "（离线规则模式）" if item["fallback"] else ""
            lines += [f"## {item['label']}{badge}", "", item["text"], ""]
        return "\n".join(lines)

    def session_messages(self, session_id: str, limit: int = 30) -> list[dict]:
        return store.recent_messages(session_id, limit)

    def clear(self, session_id: str) -> int:
        if self.cache:
            self.cache.clear()
        return store.clear_session(session_id)


# 上游叫 MerchantOrchestrator，保留别名方便旧脚本
MerchantOrchestrator = Orchestrator
