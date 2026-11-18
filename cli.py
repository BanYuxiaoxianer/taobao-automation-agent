"""命令行入口（深加工版 / by 31）

不想开网页时用这个。基于标准库 + rich，交互菜单或一次性命令都行。

  python cli.py                      交互菜单
  python cli.py doctor               环境自检
  python cli.py ask "买家问什么时候发货"
  python cli.py lister "304不锈钢保温杯"
  python cli.py workflow 家居日用 --platform taobao
  python cli.py drafts               查看待上架清单
  python cli.py export               导出 CSV
  python cli.py seed [--force]       生成示例知识库
"""

from __future__ import annotations

import argparse
import sys

from config import DEFAULT_PLATFORM, PLATFORMS, __brand__, __version__
from orchestrator import Orchestrator

BRAND_LINE = f"商家智能运营 Agent v{__version__} · {__brand__}"


def _orchestrator() -> Orchestrator:
    return Orchestrator()


def _print(text: str) -> None:
    print(text)


def cmd_doctor(_args) -> int:
    import doctor
    ready, _ = doctor.run_all()
    return 0 if ready else 0  # 自检本身不算失败


def cmd_seed(args) -> int:
    import seed
    for line in seed.generate(force=getattr(args, "force", False)):
        _print(line)
    return 0


def cmd_ask(args) -> int:
    orch = _orchestrator()
    data = orch.chat(args.text, session_id=getattr(args, "session", None))
    _print(f"【{data['route_label']}】")
    _print(data["text"])
    if data["fallback"]:
        _print("\n（离线规则模式：未检测到可用模型）")
    return 0 if data["text"] and not data["text"].startswith("[错误]") else 1


def cmd_agent(args) -> int:
    orch = _orchestrator()
    reply = orch.run_agent(args.name, args.text)
    _print(f"【{reply.agent}】")
    _print(reply.text)
    if args.trail:
        _print("\n--- 执行轨迹 ---")
        _print(reply.trail())
    return 0 if reply.ok else 1


def cmd_workflow(args) -> int:
    orch = _orchestrator()
    result = orch.run_workflow(args.category, budget=args.budget, audience=args.audience,
                               product=args.product, platform=args.platform)
    _print(result["markdown"])
    if args.save:
        import os
        from config import OUTPUT_DIR
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        path = os.path.join(OUTPUT_DIR, f"工作流报告_{args.category}.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(result["markdown"])
        _print(f"\n已保存：{path}")
    return 0


def cmd_drafts(_args) -> int:
    from tools import execute
    data = execute("list_drafts", {})
    if not data.get("items"):
        _print("待上架清单为空。先用 `python cli.py lister \"商品名\"` 生成一条。")
        return 0
    _print(f"共 {data['count']} 条：")
    for d in data["items"]:
        _print(f"  #{d['id']} {d['name']}｜¥{d['price']}｜{d['status']}｜{d.get('title', '')[:30]}")
    return 0


def cmd_export(_args) -> int:
    from tools import execute
    data = execute("export_drafts_csv", {})
    _print(f"已导出：{data['path']}（{data['count']} 条）" if data.get("ok") else data.get("error", "导出失败"))
    return 0


def menu() -> int:
    orch = _orchestrator()
    print(BRAND_LINE)
    print("=" * 52)
    print("1. 智能对话（自动选 Agent）")
    print("2. 指定 Agent 对话")
    print("3. 一键工作流（选品→上架→合规→客服→运营）")
    print("4. 待上架清单")
    print("5. 环境自检")
    print("6. 生成示例知识库")
    print("0. 退出")
    choice = input("\n请选择：").strip()
    if choice == "1":
        text = input("问题：").strip()
        data = orch.chat(text)
        print(f"\n【{data['route_label']}】\n{data['text']}")
    elif choice == "2":
        agents = orch.list_agents()
        for i, a in enumerate(agents, 1):
            print(f"{i}. {a['name']} —— {a['desc']}")
        idx = input("编号：").strip()
        if idx.isdigit() and 1 <= int(idx) <= len(agents):
            text = input("需求：").strip()
            reply = orch.run_agent(agents[int(idx) - 1]["key"], text)
            print(f"\n{reply.text}")
        else:
            print("编号不对")
    elif choice == "3":
        category = input("品类（如 家居日用）：").strip()
        result = orch.run_workflow(category, platform=DEFAULT_PLATFORM)
        print(result["markdown"])
    elif choice == "4":
        cmd_drafts(None)
    elif choice == "5":
        import doctor
        doctor.run_all()
    elif choice == "6":
        import seed
        for line in seed.generate():
            print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python cli.py", description=BRAND_LINE)
    parser.add_argument("--version", action="version", version=BRAND_LINE)
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("doctor", help="环境自检").set_defaults(func=cmd_doctor)

    p_seed = sub.add_parser("seed", help="生成示例知识库")
    p_seed.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    p_seed.set_defaults(func=cmd_seed)

    p_ask = sub.add_parser("ask", help="智能对话（自动路由）")
    p_ask.add_argument("text", help="你的问题")
    p_ask.add_argument("--session", default=None)
    p_ask.set_defaults(func=cmd_ask)

    p_agent = sub.add_parser("agent", help="指定 Agent 对话")
    p_agent.add_argument("name", choices=["selector", "lister", "service", "analyst", "compliance"])
    p_agent.add_argument("text")
    p_agent.add_argument("--trail", action="store_true", help="显示工具调用轨迹")
    p_agent.set_defaults(func=cmd_agent)

    p_list = sub.add_parser("lister", help="快捷：用上架师写素材")
    p_list.add_argument("text")
    p_list.add_argument("--trail", action="store_true")
    p_list.set_defaults(func=lambda a: cmd_agent(argparse.Namespace(name="lister", text=a.text, trail=a.trail)))

    p_flow = sub.add_parser("workflow", help="一键工作流")
    p_flow.add_argument("category")
    p_flow.add_argument("--product", default="")
    p_flow.add_argument("--budget", default="")
    p_flow.add_argument("--audience", default="")
    p_flow.add_argument("--platform", default=DEFAULT_PLATFORM, choices=list(PLATFORMS))
    p_flow.add_argument("--save", action="store_true", help="保存为 outputs/ 下的 Markdown")
    p_flow.set_defaults(func=cmd_workflow)

    sub.add_parser("drafts", help="查看待上架清单").set_defaults(func=cmd_drafts)
    sub.add_parser("export", help="导出待上架清单 CSV").set_defaults(func=cmd_export)
    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    args = build_parser().parse_args(argv)
    if not getattr(args, "command", None):
        return menu()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
