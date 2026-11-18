"""商家智能 Agent 配置（深加工版 / by 31）

相比上游的主要变化：
1. 所有模型统一为 OpenAI 兼容协议，一处填 base_url + key 就能接任意厂商
2. 新增 ModelRoute：支持 "主模型,兜底模型" 双模型写法和空串回落
3. 不再依赖本地 Ollama —— 没有 GPU 也能跑（默认全部走云端）
4. 新增 .env 解析容错：行内注释、export 前缀、BOM、引号
5. 新增 PLATFORMS 平台规则表，供上架 / 客服 / 合规检查使用
"""

import json
import logging
import os
from typing import Iterable

logger = logging.getLogger(__name__)

__version__ = "0.0.1"
__brand__ = "by 31"


# ─────────────────────────── .env 加载 ───────────────────────────

def _strip_inline_comment(value: str) -> str:
    """去掉行内注释，但保留引号里的 #"""
    if value and value[0] in "\"'":
        quote = value[0]
        end = value.rfind(quote)
        if end > 0:
            return value[1:end]
    pos = value.find(" #")
    return value[:pos].strip() if pos > 0 else value


def load_env(path: str) -> int:
    """极简 .env 解析器：支持 export 前缀 / 行内注释 / 引号，已存在的环境变量不覆盖。"""
    if not os.path.exists(path):
        return 0
    loaded = 0
    with open(path, encoding="utf-8-sig") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[7:]
            key, value = line.split("=", 1)
            key = key.strip()
            value = _strip_inline_comment(value.strip())
            if key and key not in os.environ:
                os.environ[key] = value
                loaded += 1
    return loaded


_ENV_LOADED = load_env(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))


# ─────────────────────────── 基础工具 ───────────────────────────

def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "y", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        logger.warning("配置项 %s 不是整数，回落到默认值 %s", name, default)
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "").strip() or default)
    except ValueError:
        logger.warning("配置项 %s 不是数字，回落到默认值 %s", name, default)
        return default


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


# ─────────────────────────── 模型路由 ───────────────────────────

def parse_route(spec: str) -> list[tuple[str, str]]:
    """把 "deepseek:deepseek-chat,ollama:qwen2:7b" 拆成 [(provider, model), ...]"""
    routes: list[tuple[str, str]] = []
    for chunk in (spec or "").split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        provider, _, model = chunk.partition(":")
        provider = provider.strip().lower()
        model = model.strip()
        if provider and model:
            routes.append((provider, model))
    return routes


def route_to_spec(routes: Iterable[tuple[str, str]]) -> str:
    return ",".join(f"{p}:{m}" for p, m in routes)


# ─────────────────────────── LLM ───────────────────────────

DEFAULT_PROVIDER = _env_str("DEFAULT_PROVIDER", "deepseek").lower()

# DeepSeek（云端，稳定、便宜）
DEEPSEEK_API_KEY = _env_str("DEEPSEEK_API_KEY")
DEEPSEEK_API_BASE = _env_str("DEEPSEEK_API_BASE", "https://api.deepseek.com")
DEEPSEEK_MODEL = _env_str("DEEPSEEK_MODEL", "deepseek-chat")

# OpenAI 兼容（通义 / 智谱 / Moonshot / 硅基流动 / vLLM / LM Studio …）
COMPAT_API_KEY = _env_str("COMPAT_API_KEY")
COMPAT_API_BASE = _env_str("COMPAT_API_BASE", "https://api.siliconflow.cn")
COMPAT_MODEL = _env_str("COMPAT_MODEL", "Qwen/Qwen2.5-7B-Instruct")

# Ollama（本地，有 GPU 才推荐）
OLLAMA_BASE = _env_str("OLLAMA_BASE", "http://localhost:11434")
OLLAMA_MODEL = _env_str("OLLAMA_MODEL", "qwen2:7b")
OLLAMA_FAST_MODEL = _env_str("OLLAMA_FAST_MODEL", "qwen2:1.5b")

PROVIDERS: dict[str, dict] = {
    "deepseek": {
        "label": "DeepSeek",
        "base_url": DEEPSEEK_API_BASE.rstrip("/"),
        "api_key": DEEPSEEK_API_KEY,
        "default_model": DEEPSEEK_MODEL,
        "path": "/chat/completions",
    },
    "compat": {
        "label": "OpenAI 兼容",
        "base_url": COMPAT_API_BASE.rstrip("/"),
        "api_key": COMPAT_API_KEY,
        "default_model": COMPAT_MODEL,
        "path": "/v1/chat/completions",
    },
    "ollama": {
        "label": "Ollama 本地",
        "base_url": OLLAMA_BASE.rstrip("/"),
        "api_key": "",
        "default_model": OLLAMA_MODEL,
        "path": "/api/chat",
    },
}

# 每个 Agent 的模型链：逗号分隔，前一个失败自动切下一个
# 想全局改成本地模型，把 LLM_PROFILE=local 写进 .env 即可
LLM_PROFILE = _env_str("LLM_PROFILE", "cloud").lower()
_PROFILE_MAP = {
    "cloud": "deepseek:deepseek-chat",
    "local": "ollama:qwen2:7b",
    "compat": "compat:Qwen/Qwen2.5-7B-Instruct",
}
_DEFAULT_CHAIN = _env_str("LLM_CHAIN", _PROFILE_MAP.get(LLM_PROFILE, _PROFILE_MAP["cloud"]))

AGENT_CHAIN = {
    "router": _env_str("ROUTER_MODEL", _DEFAULT_CHAIN),
    "selector": _env_str("SELECTOR_MODEL", _DEFAULT_CHAIN),
    "lister": _env_str("LISTER_MODEL", _DEFAULT_CHAIN),
    "service": _env_str("SERVICE_MODEL", _DEFAULT_CHAIN),
    "analyst": _env_str("ANALYST_MODEL", _DEFAULT_CHAIN),
    "compliance": _env_str("COMPLIANCE_MODEL", _DEFAULT_CHAIN),
    "fast": _env_str("FAST_MODEL", _DEFAULT_CHAIN),
}

# 兼容上游 config 的名字（老代码 / 老测试会读这两个字典）
AGENT_MODEL = {k: v.split(",")[0] for k, v in AGENT_CHAIN.items()}
AGENT_LIGHT_MODEL = {k: AGENT_CHAIN["fast"] for k in AGENT_CHAIN}


# ─────────────────────────── 运行参数 ───────────────────────────

AGENT_TIMEOUT = _env_int("AGENT_TIMEOUT", 300)
CONNECT_TIMEOUT = _env_int("CONNECT_TIMEOUT", 10)
MAX_RETRIES = _env_int("MAX_RETRIES", 2)
REACT_MAX_ROUNDS = _env_int("REACT_MAX_ROUNDS", 8)

RAG_TOP_K = _env_int("RAG_TOP_K", 3)
RAG_MIN_SCORE = _env_float("RAG_MIN_SCORE", 0.15)

CACHE_ENABLED = _env_bool("CACHE_ENABLED", True)
CACHE_TTL = _env_int("CACHE_TTL", 3600)
CACHE_CAPACITY = _env_int("CACHE_CAPACITY", 200)

RATE_LIMIT_ENABLED = _env_bool("RATE_LIMIT_ENABLED", True)
RATE_LIMIT_MAX = _env_int("RATE_LIMIT_MAX", 30)
RATE_LIMIT_WINDOW = _env_int("RATE_LIMIT_WINDOW", 60)

LOG_LEVEL = _env_str("LOG_LEVEL", "INFO").upper()
LOG_FORMAT = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

WEB_PORT = _env_int("WEB_PORT", 7860)
WEB_HOST = _env_str("WEB_HOST", "127.0.0.1")
APP_TITLE = _env_str("APP_TITLE", "商家智能运营 Agent")

# 本工具只在本机使用，默认不设登录。若真要放到局域网/公网，
# 把 AUTH_ENABLED 打开并自行在 webui.py 里接一层鉴权，不要使用任何内置默认口令。
AUTH_ENABLED = _env_bool("AUTH_ENABLED", False)


# ─────────────────────────── 数据存储 ───────────────────────────

DATA_DIR = _env_str("DATA_DIR",
                    os.environ.get("MERCHANT_DATA_DIR")
                    or os.path.join(os.path.dirname(os.path.abspath(__file__)), "data"))
OUTPUT_DIR = _env_str("OUTPUT_DIR",
                      os.path.join(os.environ.get("MERCHANT_DATA_DIR") or DATA_DIR, "..", "outputs")
                      if os.environ.get("MERCHANT_DATA_DIR")
                      else os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs"))
OUTPUT_DIR = os.path.abspath(OUTPUT_DIR)

# 对话记录：sqlite（默认，零依赖）/ json / mysql
CONVERSATION_BACKEND = _env_str("CONVERSATION_BACKEND", "sqlite").lower()

# 向量检索：auto（能起就用，起不来降级）/ milvus / memory
VECTOR_BACKEND = _env_str("VECTOR_BACKEND", "auto").lower()
MILVUS_HOST = _env_str("MILVUS_HOST", "localhost")
MILVUS_PORT = _env_int("MILVUS_PORT", 19530)
MILVUS_COLLECTION = _env_str("MILVUS_COLLECTION", "conversation_embeddings")

MYSQL_HOST = _env_str("MYSQL_HOST", "localhost")
MYSQL_PORT = _env_int("MYSQL_PORT", 3307)
MYSQL_USER = _env_str("MYSQL_USER", "merchant")
MYSQL_PASSWORD = _env_str("MYSQL_PASSWORD", "merchant_pass_2024")
MYSQL_DATABASE = _env_str("MYSQL_DATABASE", "merchant_agent")


# ─────────────────────────── 平台规则 ───────────────────────────

PLATFORMS: dict[str, dict] = {
    "taobao": {
        "label": "淘宝",
        "title_max": 30,
        "title_formula": "核心词 + 属性词 + 场景词 + 卖点词（搜索流量优先，流量词前置）",
        "forbidden": ["第一", "最好", "最强", "国家级", "世界级", "顶级", "史无前例", "独家", "万能"],
        "detail_fields": ["材质", "尺寸", "重量", "颜色", "包装清单", "适用人群"],
    },
    "pdd": {
        "label": "拼多多",
        "title_max": 30,
        "title_formula": "卖点词 + 核心词 + 促销词（突出性价比与促销感）",
        "forbidden": ["第一", "最好", "国家级", "顶级", "万能"],
        "detail_fields": ["材质", "规格", "颜色", "包装", "发货地"],
    },
    "douyin": {
        "label": "抖音小店",
        "title_max": 30,
        "title_formula": "痛点词 + 效果词 + 核心词（偏情绪与场景）",
        "forbidden": ["第一", "最好", "国家级", "顶级"],
        "detail_fields": ["材质", "规格", "使用场景", "适用人群", "发货时效"],
    },
}
DEFAULT_PLATFORM = _env_str("DEFAULT_PLATFORM", "taobao").lower()
if DEFAULT_PLATFORM not in PLATFORMS:
    DEFAULT_PLATFORM = "taobao"


# ─────────────────────────── 自检 ───────────────────────────

def _ollama_alive() -> bool:
    """Ollama 没有 API Key，只能真连一下才知道在不在。结果缓存，避免反复探测。"""
    global _OLLAMA_CACHE
    if _OLLAMA_CACHE is not None:
        return bool(_OLLAMA_CACHE)
    alive = False
    try:
        import httpx
        resp = httpx.get(PROVIDERS["ollama"]["base_url"] + "/api/tags",
                         timeout=min(CONNECT_TIMEOUT, 2))
        alive = resp.status_code == 200
    except Exception:  # noqa: BLE001 - 连不上就是没装，正常情况
        alive = False
    _OLLAMA_CACHE = alive
    return alive


_OLLAMA_CACHE: bool | None = None


def provider_ready(provider: str) -> bool:
    cfg = PROVIDERS.get(provider)
    if not cfg:
        return False
    if provider == "ollama":
        return _ollama_alive()
    return bool(cfg["api_key"])


def first_ready_provider() -> str:
    for name in ("deepseek", "compat", "ollama"):
        if provider_ready(name):
            return name
    return ""


def describe_routes() -> str:
    lines = []
    for agent, chain in AGENT_CHAIN.items():
        routes = parse_route(chain)
        marks = "".join("✅" if provider_ready(p) else "❌" for p, _ in routes)
        lines.append(f"  {agent:<10} {chain:<40} {marks}")
    return "\n".join(lines)


if _ENV_LOADED:
    logger.debug("已从 .env 载入 %s 项配置", _ENV_LOADED)


# ─────────────────────── 运行期设置（网页里填 Key） ───────────────────────
# 存在 data/settings.json，只有本机可读；不落系统环境变量，也不写配置文件式的密钥。
# 网页 GET /api/settings 只返回掩码，原 Key 永远不出后端。

SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")
_SETTINGS_FIELDS = ("deepseek_api_key", "deepseek_api_base", "deepseek_model")


def load_settings() -> dict:
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(patch: dict) -> dict:
    merged = load_settings()
    for field, value in patch.items():
        if field in _SETTINGS_FIELDS:
            merged[field] = (value or "").strip()
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(merged, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, SETTINGS_PATH)
    apply_settings(merged)
    return merged


def apply_settings(settings: dict | None = None) -> None:
    current = load_settings() if settings is None else settings
    key = current.get("deepseek_api_key") or DEEPSEEK_API_KEY
    base = current.get("deepseek_api_base") or DEEPSEEK_API_BASE
    model = current.get("deepseek_model") or DEEPSEEK_MODEL
    PROVIDERS["deepseek"]["api_key"] = key
    PROVIDERS["deepseek"]["base_url"] = base.rstrip("/")
    PROVIDERS["deepseek"]["default_model"] = model


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return key[:2] + "…"
    return key[:5] + "…" + key[-4:]


def public_settings() -> dict:
    saved = load_settings()
    key = PROVIDERS["deepseek"]["api_key"]
    if saved.get("deepseek_api_key"):
        source = "settings.json"
    elif DEEPSEEK_API_KEY:
        source = "env"
    else:
        source = ""
    return {
        "deepseek": {
            "configured": bool(key),
            "mask": mask_key(key),
            "base_url": PROVIDERS["deepseek"]["base_url"],
            "model": PROVIDERS["deepseek"]["default_model"],
            "source": source,
        }
    }


def update_settings(api_key: str | None = None, base_url: str | None = None,
                    model: str | None = None) -> dict:
    """网页保存入口：空字符串 / None 表示这一项不改。"""
    patch: dict = {}
    if api_key is not None:
        key = api_key.strip()
        if key:
            if not key.startswith("sk-"):
                raise ValueError("DeepSeek 的 Key 通常以 sk- 开头，请确认复制完整")
            if len(key) < 20:
                raise ValueError("Key 太短，看起来没复制完整")
            patch["deepseek_api_key"] = key
    if base_url and base_url.strip():
        patch["deepseek_api_base"] = base_url.strip()
    if model and model.strip():
        patch["deepseek_model"] = model.strip()
    if patch:
        save_settings(patch)
    return public_settings()["deepseek"]


apply_settings()
