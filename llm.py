"""LLM 调用层（深加工版 / by 31）

设计目标：
- 一个入口 call_messages()，三种后端（deepseek / compat / ollama）共用同一套消息格式
- 主模型失败自动切兜底模型，切了谁、为什么切，全部写进监控
- 统一返回 CallResult，永远不抛异常（Agent 层只判断 result.ok）
- 流式输出用 stream_messages()，只在终端 / Web 需要"打字机效果"时用
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import httpx

from config import (
    AGENT_CHAIN,
    AGENT_TIMEOUT,
    CONNECT_TIMEOUT,
    PROVIDERS,
    parse_route,
    provider_ready,
)

logger = logging.getLogger(__name__)

_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(
            timeout=httpx.Timeout(AGENT_TIMEOUT, connect=CONNECT_TIMEOUT),
            limits=httpx.Limits(max_connections=16, max_keepalive_connections=8),
        )
    return _client


def close_client() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None


# ─────────────────────────── 结果对象 ───────────────────────────

@dataclass
class CallResult:
    ok: bool = True
    text: str = ""
    provider: str = ""
    model: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    attempts: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


def _fail(message: str, attempts: list[str]) -> CallResult:
    """统一失败信息前缀：Agent 靠 [错误] / [超时] 判断是否继续重试"""
    return CallResult(ok=False, text=message, error=message, attempts=attempts)


# ─────────────────────────── 请求体 ───────────────────────────

def _openai_payload(model: str, messages: list[dict], temperature: float,
                    max_tokens: int, tools: list[dict] | None, stream: bool) -> dict:
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
    if stream:
        # 有的网关不认 stream_options，只在 OpenAI 系里加
        payload["stream_options"] = {"include_usage": False}
    return payload


def _ollama_messages(messages: list[dict]) -> tuple[list[dict], dict]:
    """Ollama 用 system 字段 + tools 结构略有不同，这里做一次归一化"""
    system_parts = [m["content"] for m in messages if m.get("role") == "system"]
    converted = [m for m in messages if m.get("role") != "system"]
    for m in converted:
        if "tool_calls" in m:
            m["tool_calls"] = [
                {
                    "function": {
                        "name": tc.get("function", {}).get("name", ""),
                        "arguments": tc.get("function", {}).get("arguments", {}),
                    }
                }
                for tc in m["tool_calls"]
            ]
    extra = {"options": {"num_predict": 4096}}
    if system_parts:
        extra["system"] = "\n\n".join(system_parts)
    return converted, extra


def _normalize_tool_calls(raw: Any) -> list[dict]:
    """把两种后端的 tool_calls 统一成 [{id,name,arguments(dict)}]"""
    out: list[dict] = []
    for tc in raw or []:
        fn = tc.get("function") or {}
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args) if args.strip() else {}
            except json.JSONDecodeError:
                args = {"_raw": args}
        elif args is None:
            args = {}
        out.append({
            "id": tc.get("id", ""),
            "name": fn.get("name", ""),
            "arguments": args,
        })
    return out


# ─────────────────────────── 单次调用 ───────────────────────────

def _call_once(provider: str, model: str, messages: list[dict], temperature: float,
               max_tokens: int, tools: list[dict] | None) -> CallResult:
    cfg = PROVIDERS[provider]
    base = cfg["base_url"]
    is_ollama = provider == "ollama"
    headers = {"Content-Type": "application/json"}
    if not is_ollama and cfg["api_key"]:
        headers["Authorization"] = f"Bearer {cfg['api_key']}"

    payload = _openai_payload(model, messages, temperature, max_tokens, tools, False)
    if is_ollama:
        converted, extra = _ollama_messages(payload["messages"])
        payload = {"model": model, "messages": converted, "stream": False, **extra}
        if tools:
            payload["tools"] = tools

    started = time.perf_counter()
    try:
        resp = _get_client().post(f"{base}{cfg['path']}", json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        logger.warning("LLM 超时 %s:%s -> %s", provider, model, exc)
        return _fail(f"[超时] {provider}:{model} 调用超过 {AGENT_TIMEOUT}s", [f"{provider}:{model} 超时"])
    except httpx.HTTPError as exc:
        logger.warning("LLM 网络错误 %s:%s -> %s", provider, model, exc)
        return _fail(f"[错误] {provider}:{model} 网络异常：{type(exc).__name__}", [f"{provider}:{model} 网络异常"])

    duration = int((time.perf_counter() - started) * 1000)

    if resp.status_code >= 400:
        snippet = resp.text[:200].replace("\n", " ")
        logger.warning("LLM HTTP %s %s:%s -> %s", resp.status_code, provider, model, snippet)
        return _fail(f"[错误] {provider}:{model} 返回 HTTP {resp.status_code}", [f"{provider}:{model} HTTP {resp.status_code}"])

    try:
        data = resp.json()
    except json.JSONDecodeError:
        return _fail(f"[错误] {provider}:{model} 返回不是 JSON", [f"{provider}:{model} 非 JSON"])

    if is_ollama:
        message = data.get("message") or {}
        text = message.get("content", "")
        tool_calls = _normalize_tool_calls(message.get("tool_calls"))
        prompt_tokens = data.get("prompt_eval_count", 0)
        completion_tokens = data.get("eval_count", 0)
    else:
        choices = data.get("choices") or []
        if not choices:
            return _fail(f"[错误] {provider}:{model} 没有返回 choices", [f"{provider}:{model} 空 choices"])
        message = choices[0].get("message") or {}
        text = message.get("content") or ""
        tool_calls = _normalize_tool_calls(message.get("tool_calls"))
        usage = data.get("usage") or {}
        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)

    return CallResult(
        ok=True, text=text, provider=provider, model=model,
        tool_calls=tool_calls, prompt_tokens=prompt_tokens or 0,
        completion_tokens=completion_tokens or 0, duration_ms=duration,
    )


# ─────────────────────────── 对外入口 ───────────────────────────

def call_messages(messages: list[dict], *, agent: str = "default", model_chain: str | None = None,
                  temperature: float = 0.7, max_tokens: int = 4096,
                  tools: list[dict] | None = None, session_id: str = "",
                  monitor=None) -> CallResult:
    """按模型链依次尝试，第一个成功的直接返回。"""
    chain = parse_route(model_chain or AGENT_CHAIN.get(agent) or AGENT_CHAIN["fast"])
    if not chain:
        return _fail("[错误] 没有配置任何模型，请检查 .env", [])

    attempts: list[str] = []
    for provider, model in chain:
        if not provider_ready(provider):
            attempts.append(f"{provider}:{model} 未配置密钥")
            continue
        result = _call_once(provider, model, messages, temperature, max_tokens, tools)
        attempts.extend(result.attempts)
        result.attempts = attempts
        if monitor is not None:
            monitor.record(provider=provider, model=model, agent=agent, session_id=session_id,
                           prompt_tokens=result.prompt_tokens, completion_tokens=result.completion_tokens,
                           duration_ms=result.duration_ms, success=result.ok, error=result.error)
        if result.ok:
            if attempts:
                logger.info("模型兜底成功：%s → %s", attempts[:-1] or "首个", f"{provider}:{model}")
            return result

    return _fail(
        "[错误] 所有模型都调用失败：\n  " + "\n  ".join(attempts or ["未配置任何可用模型"]),
        attempts,
    )


def stream_messages(messages: list[dict], *, agent: str = "default", model_chain: str | None = None,
                    temperature: float = 0.7, max_tokens: int = 4096) -> Iterator[str]:
    """逐段 yield 文本；任何异常都退化成一次性结果，不让界面卡死。"""
    chain = parse_route(model_chain or AGENT_CHAIN.get(agent) or AGENT_CHAIN["fast"])
    for provider, model in chain:
        if not provider_ready(provider):
            continue
        cfg = PROVIDERS[provider]
        headers = {"Content-Type": "application/json"}
        if provider != "ollama" and cfg["api_key"]:
            headers["Authorization"] = f"Bearer {cfg['api_key']}"
        if provider == "ollama":
            payload = {"model": model, "messages": messages, "stream": True}
        else:
            payload = _openai_payload(model, messages, temperature, max_tokens, None, True)
        try:
            with _get_client().stream("POST", f"{cfg['base_url']}{cfg['path']}",
                                      json=payload, headers=headers) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if line in ("[DONE]", ""):
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if provider == "ollama":
                        piece = (chunk.get("message") or {}).get("content", "")
                    else:
                        choices = chunk.get("choices") or []
                        piece = ((choices[0].get("delta") or {}).get("content") or "") if choices else ""
                    if piece:
                        yield piece
            return
        except Exception as exc:  # noqa: BLE001 - 流式中任何异常都降级
            logger.warning("流式调用失败 %s:%s -> %s", provider, model, exc)
            continue
    yield ""


def simple_prompt(system: str, user: str, *, temperature: float = 0.7, agent: str = "fast") -> str:
    """一句话问答：给路由、分类、摘要这类不需要工具的场合用"""
    result = call_messages(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
        agent=agent, temperature=temperature, max_tokens=2048,
    )
    return result.text if result.ok else result.text


# ─────────────────────────── 健康检查 ───────────────────────────

def check_provider(provider: str) -> tuple[bool, str]:
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return False, f"未知提供者 {provider}"
    if provider == "ollama":
        try:
            resp = _get_client().get(f"{cfg['base_url']}/api/tags", timeout=5)
            if resp.status_code == 200:
                models = [m.get("name", "") for m in resp.json().get("models", [])]
                return True, f"Ollama 在线，已加载 {len(models)} 个模型"
            return False, f"Ollama 返回 HTTP {resp.status_code}"
        except httpx.HTTPError as exc:
            return False, f"Ollama 连不上：{type(exc).__name__}"
    if not cfg["api_key"]:
        return False, f"{cfg['label']} 未配置 API Key"
    result = call_messages(
        [{"role": "user", "content": "回复 OK 两个字母即可"}],
        model_chain=f"{provider}:{cfg['default_model']}", max_tokens=8, temperature=0,
    )
    return (result.ok, f"{cfg['label']} 连通（{result.model}）") if result.ok else (False, result.text)


def check_llm() -> tuple[bool, str]:
    """顺序探测，返回第一个可用的提供者"""
    messages = []
    for name in PROVIDERS:
        if not provider_ready(name):
            messages.append(f"{PROVIDERS[name]['label']}: 未配置")
            continue
        ok, detail = check_provider(name)
        if ok:
            return True, detail
        messages.append(detail)
    return False, "；".join(messages) or "没有任何可用模型"
