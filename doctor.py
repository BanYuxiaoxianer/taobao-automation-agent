"""启动自检（深加工版 / by 31）

上游把「能不能跑」的答案藏在第一个报错里。这里提前体检一遍，
缺什么、怎么补，直接在终端 / 网页上告诉用户。
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys

from config import (
    AGENT_CHAIN, DATA_DIR, DEFAULT_PLATFORM, LLM_PROFILE, OUTPUT_DIR,
    PLATFORMS, PROVIDERS, describe_routes, first_ready_provider, provider_ready,
)

OK = "✅"
WARN = "⚠️"
BAD = "❌"

REQUIRED = [("httpx", "HTTP 调用模型"), ("rich", "终端彩色菜单")]
OPTIONAL = [
    ("gradio", "网页界面（python webui.py 才需要）"),
    ("pytest", "跑测试"),
]


def _has(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError):
        return False


def check_python() -> tuple[bool, str]:
    if sys.version_info < (3, 10):
        return False, f"Python {sys.version.split()[0]} 太低，本项目要求 3.10+"
    return True, f"Python {sys.version.split()[0]}"


def check_deps() -> list[str]:
    lines = []
    for module, why in REQUIRED:
        lines.append(f"{OK if _has(module) else BAD} 依赖 {module}（{why}）")
    for module, why in OPTIONAL:
        lines.append(f"{OK if _has(module) else WARN} 可选 {module}（{why}）")
    return lines


def check_dirs() -> list[str]:
    lines = []
    for path in (DATA_DIR, OUTPUT_DIR):
        try:
            os.makedirs(path, exist_ok=True)
            probe = os.path.join(path, ".write_test")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            lines.append(f"{OK} 目录可写 {path}")
        except OSError as exc:
            lines.append(f"{BAD} 目录不可写 {path}：{exc}")
    return lines


def check_llm() -> tuple[bool, list[str]]:
    lines = [f"LLM 档位：{LLM_PROFILE}"]
    lines.append(f"模型路由：\n{describe_routes()}")
    first = first_ready_provider()
    if first:
        lines.append(f"{OK} 至少有一个可用提供者：{PROVIDERS[first]['label']}")
        return True, lines
    lines.append(f"{WARN} 没有任何模型配置好 → 会以离线规则模式运行")
    lines.append("   补法：复制 .env.example 为 .env，填 DEEPSEEK_API_KEY 或 COMPAT_API_KEY")
    return False, lines


def check_store() -> tuple[bool, str]:
    try:
        import store
        ok, detail = store.db_health()
        return ok, detail
    except Exception as exc:  # noqa: BLE001
        return False, f"SQLite 初始化失败：{exc}"


def check_agents() -> list[str]:
    lines = []
    try:
        import orchestrator
        orch = orchestrator.Orchestrator()
        for info in orch.list_agents():
            ready = "模型就绪" if any(provider_ready(p) for p, _ in
                                     __import__("config").parse_route(info["model"])) else "离线模式"
            lines.append(f"{OK} Agent {info['name']}（{ready}，工具 {len(info['tools'])} 个）")
        lines.append(f"{OK} 平台规则：{' / '.join(v['label'] for v in PLATFORMS.values())}（默认 {DEFAULT_PLATFORM}）")
    except Exception as exc:  # noqa: BLE001
        lines.append(f"{BAD} Agent 装载失败：{type(exc).__name__}: {exc}")
    return lines


def run_all(verbose: bool = True) -> tuple[bool, str]:
    ok_py, py_line = check_python()
    lines = ["## 环境自检", f"{OK if ok_py else BAD} {py_line}", "", "### 依赖"]
    lines += check_deps()
    lines += ["", "### 目录"] + check_dirs()
    llm_ok, llm_lines = check_llm()
    lines += ["", "### 模型"] + llm_lines
    store_ok, store_line = check_store()
    lines += ["", "### 存储", f"{OK if store_ok else BAD} {store_line}", "", "### Agent"]
    lines += check_agents()

    all_ok = ok_py and llm_ok
    lines = [
        "",
        "### 结论",
        f"{OK} 可以直接开工" if all_ok else f"{WARN} 能启动，但 AI 生成会走离线规则模式（填个 API Key 就满血）",
        "",
        f"模型链参考：{AGENT_CHAIN['fast']}",
    ]
    text = "\n".join(lines)
    if verbose:
        print(text)
    # 没有模型可用时退出码为 2，方便脚本判断；但不算「程序出错」
    return all_ok, text


if __name__ == "__main__":
    import sys
    # Windows 控制台默认 GBK，遇到 ✅ 这类字符会直接崩，这里强制 UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    ready, _ = run_all()
    sys.exit(0 if ready else 2)
