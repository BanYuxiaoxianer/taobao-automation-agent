"""网页界面（深加工版 / by 31）

上游用 Gradio 5 + 10 个 Tab，装不上依赖就什么都看不到。
这里改成**只用标准库 http.server** 起一个单页应用：

- 零额外依赖（pip install 只要 httpx + rich）
- 左边选 Agent，右边对话；上方能看模型状态、成本、草稿清单
- 接口同时可被别的程序调用（见 /api/*）

启动：python webui.py           然后打开 http://127.0.0.1:7860
"""

from __future__ import annotations

import json
import logging
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import store
from config import AGENT_CHAIN, CONVERSATION_BACKEND, DEFAULT_PLATFORM, PLATFORMS, WEB_PORT, __brand__, __version__
from orchestrator import Orchestrator

logger = logging.getLogger(__name__)
ORCH = Orchestrator()

PAGE = r"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>商家智能运营 Agent</title>
<style>
  :root{--bg:#faf8f5;--panel:#ffffff;--line:#e3e6ec;--text:#1f2430;--dim:#6b7280;
        --accent:#e8590c;--accent-soft:#fdf0e8;--ok:#12a150;--warn:#b7791f;--err:#d64545;
        --user-bg:#eef4ff;--user-line:#d6e4ff;--hover:#f0f2f6}
  *{box-sizing:border-box}
  body{margin:0;font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif;background:var(--bg);color:var(--text)}
  header{display:flex;align-items:center;gap:12px;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--panel)}
  header h1{font-size:15px;margin:0;font-weight:600}
  header .ver{color:var(--dim);font-size:12px}
  header .right{margin-left:auto;display:flex;gap:8px;align-items:center;font-size:12px;color:var(--dim)}
  .dot{width:8px;height:8px;border-radius:50%;display:inline-block;margin-right:4px}
  main{display:grid;grid-template-columns:220px 1fr 300px;height:calc(100vh - 53px)}
  aside,section,aside.right{overflow-y:auto;padding:14px;background:var(--panel)}
  aside{border-right:1px solid var(--line)}
  aside.right{border-left:1px solid var(--line)}
  .agent{padding:9px 10px;border-radius:8px;cursor:pointer;border:1px solid transparent;margin-bottom:4px}
  .agent:hover{background:var(--hover)}
  .agent.on{background:var(--accent-soft);border-color:var(--accent)}
  .agent b{display:block;font-size:13px}
  .agent span{font-size:11px;color:var(--dim)}
  section{display:flex;flex-direction:column;padding:0;background:var(--bg)}
  #log{flex:1;overflow-y:auto;padding:16px 20px}
  .msg{margin-bottom:16px;display:flex;flex-direction:column;gap:4px}
  .msg .who{font-size:11px;color:var(--dim)}
  .msg .body{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:10px 12px;
             white-space:pre-wrap;box-shadow:0 1px 2px rgba(16,24,40,.04)}
  .msg.user{align-items:flex-end}
  .msg.user .body{background:var(--user-bg);border-color:var(--user-line)}
  .msg .trail{font-size:11px;color:var(--dim);white-space:pre-wrap;border-left:2px solid var(--line);padding-left:8px}
  .badge{display:inline-block;font-size:11px;padding:1px 6px;border-radius:5px;background:#fff6e5;color:var(--warn);margin-left:6px}
  footer{border-top:1px solid var(--line);padding:12px 16px;display:flex;gap:8px;background:var(--panel)}
  textarea{flex:1;resize:none;height:60px;background:#fff;color:var(--text);border:1px solid var(--line);
           border-radius:8px;padding:8px 10px;font:inherit;outline:none}
  textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px rgba(232,89,12,.10)}
  button{background:var(--accent);color:#fff;border:0;border-radius:8px;padding:0 18px;font:inherit;cursor:pointer}
  button.ghost{background:#fff;color:var(--text);border:1px solid var(--line)}
  button.ghost:hover{background:var(--hover)}
  button:disabled{opacity:.5;cursor:not-allowed}
  .kv{display:flex;justify-content:space-between;font-size:12px;padding:3px 0;border-bottom:1px dashed var(--line)}
  .kv span:last-child{color:var(--dim)}
  h3{font-size:12px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;margin:16px 0 8px}
  h3:first-child{margin-top:0}
  .draft{font-size:12px;padding:6px 0;border-bottom:1px dashed var(--line)}
  .draft b{color:var(--text)}
  .empty{color:var(--dim);font-size:12px}

.cfg{display:flex;flex-direction:column;gap:6px}
.cfg input{width:100%;box-sizing:border-box;padding:7px 9px;border:1px solid var(--line);border-radius:8px;background:var(--panel);color:var(--text);font:inherit;font-size:13px}
.cfg input:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px rgba(232,89,12,.10)}
.cfg .row{display:flex;gap:6px}
.cfg .row.vert{flex-direction:column}
.cfg .row input{flex:1;min-width:0}
.cfg button{padding:7px 10px;border-radius:8px;border:1px solid transparent;background:var(--accent);color:#fff;font:inherit;font-size:13px;cursor:pointer}
.cfg button.ghost{background:var(--panel);border-color:var(--line);color:var(--text)}
.cfg button:disabled{opacity:.6;cursor:default}
.cfg .hint{font-size:12px;line-height:1.6;color:var(--dim)}
.cfg .hint.ok{color:var(--ok)}
.cfg .hint.err{color:var(--err)}
</style>
</head>
<body>
<header>
  <h1>商家智能运营 Agent</h1>
  <span class="ver">v__VERSION__</span>
  <div class="right">
    <span id="llm-state"><span class="dot" style="background:#9aa3b2"></span>检测中</span>
    <button class="ghost" onclick="doctor()">环境自检</button>
  </div>
</header>
<main>
  <aside id="agents"></aside>
  <section>
    <div id="log"></div>
    <footer>
      <textarea id="input" placeholder="例如：帮我给一款睡衣写淘宝标题和卖点 / 买家问什么时候发货怎么回"></textarea>
      <button id="send" onclick="send()">发送</button>
    </footer>
  </section>
  <aside class="right">
    <h3>模型设置（DeepSeek）</h3>
    <div class="cfg">
      <input id="cfg-key" type="password" autocomplete="off" spellcheck="false" placeholder="粘贴 sk- 开头的 API Key">
      <div class="row vert">
        <input id="cfg-base" type="text" spellcheck="false" placeholder="接口地址">
        <input id="cfg-model" type="text" spellcheck="false" placeholder="模型名">
      </div>
      <div class="row">
        <button id="cfg-save" onclick="saveKey()">保存并启用</button>
        <button class="ghost" id="cfg-test" onclick="testKey()">测试连接</button>
      </div>
      <div class="hint" id="cfg-hint">读取中…</div>
    </div>
    <h3>模型路由</h3>
    <div id="routes"></div>
    <h3>今日用量</h3>
    <div id="usage"></div>
    <h3>待上架清单</h3>
    <div id="drafts"><div class="empty">暂无</div></div>
  </aside>
</main>
<script>
const state = {route:"auto", session:null, busy:false};
function esc(s){return (s||"").replace(/[&<>]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}
function add(role, text, trail, fb){
  const log = document.getElementById("log");
  const div = document.createElement("div");
  div.className = "msg " + role;
  const who = role === "user" ? "我" : (role === "agent" ? "助手" : "系统");
  div.innerHTML = `<div class="who">${who}${fb?'<span class="badge">离线规则模式</span>':''}</div>`
    + `<div class="body">${esc(text)}</div>`
    + (trail ? `<div class="trail">${esc(trail)}</div>` : "");
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}
async function api(path, body){
  const res = await fetch(path, {method: body ? "POST" : "GET",
    headers: {"Content-Type":"application/json"},
    body: body ? JSON.stringify(body) : undefined});
  return res.json();
}
async function loadAgents(){
  const data = await api("/api/agents");
  const box = document.getElementById("agents");
  box.innerHTML = `<div class="agent on" data-r="auto" onclick="pick('auto')"><b>智能对话</b><span>自动判断意图</span></div>`
    + data.agents.map(a => `<div class="agent" data-r="${a.key}" onclick="pick('${a.key}')">
        <b>${esc(a.name)}</b><span>${esc(a.desc)}</span></div>`).join("");
}
function pick(route){
  state.route = route;
  document.querySelectorAll(".agent").forEach(el => el.classList.toggle("on", el.dataset.r === route));
}
async function loadStatus(){
  const s = await api("/api/status");
  const el = document.getElementById("llm-state");
  el.innerHTML = s.llm.ready
    ? `<span class="dot" style="background:var(--ok)"></span>${esc(s.llm.label)}`
    : `<span class="dot" style="background:var(--warn)"></span>离线规则模式`;
  document.getElementById("routes").innerHTML = Object.entries(s.routes)
    .map(([k,v]) => `<div class="kv"><span>${k}</span><span>${esc(v.chain)}</span></div>`).join("");
  const u = s.usage;
  document.getElementById("usage").innerHTML =
    `<div class="kv"><span>调用次数</span><span>${u.calls}</span></div>
     <div class="kv"><span>失败</span><span>${u.failed}</span></div>
     <div class="kv"><span>花费（元）</span><span>${u.cost}</span></div>
     <div class="kv"><span>平均耗时</span><span>${u.avg_duration_ms} ms</span></div>`;
  const d = await api("/api/drafts");
  const box = document.getElementById("drafts");
  box.innerHTML = d.items.length
    ? d.items.slice(0,8).map(x => `<div class="draft"><b>#${x.id} ${esc(x.name)}</b><br>¥${x.price} · ${esc(x.status)}</div>`).join("")
    : `<div class="empty">暂无草稿</div>`;
}
async function send(){
  if (state.busy) return;
  const box = document.getElementById("input");
  const text = box.value.trim();
  if (!text) return;
  add("user", text);
  box.value = "";
  state.busy = true; document.getElementById("send").disabled = true;
  try {
    const data = await api("/api/chat", {message: text, route: state.route === "auto" ? "" : state.route, session_id: state.session});
    state.session = data.session_id;
    add("agent", data.text, data.trail, data.fallback);
  } catch (e) {
    add("system", "请求失败：" + e);
  } finally {
    state.busy = false; document.getElementById("send").disabled = false;
    loadStatus();
  }
}
async function doctor(){
  const data = await api("/api/doctor");
  add("system", data.text);
}
document.getElementById("input").addEventListener("keydown", e => {
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) send();
});
async function loadSettings(){
  const s = await api("/api/settings");
  const d = s.deepseek || {};
  const hint = document.getElementById("cfg-hint");
  document.getElementById("cfg-base").value = d.base_url || "";
  document.getElementById("cfg-model").value = d.model || "";
  document.getElementById("cfg-key").placeholder = d.configured
    ? "已配置 " + d.mask + "，留空则不修改" : "粘贴 sk- 开头的 API Key";
  hint.className = "hint" + (d.configured ? " ok" : "");
  hint.textContent = d.configured
    ? "当前生效：" + d.mask + (d.source === "settings.json" ? "（网页设置）" : "（环境变量）")
    : "未配置，所有功能走离线规则模式";
}
async function saveKey(){
  const key = document.getElementById("cfg-key").value.trim();
  const btn = document.getElementById("cfg-save");
  const hint = document.getElementById("cfg-hint");
  btn.disabled = true;
  hint.className = "hint"; hint.textContent = "保存中…";
  try{
    const r = await api("/api/settings", {
      api_key: key,
      base_url: document.getElementById("cfg-base").value.trim(),
      model: document.getElementById("cfg-model").value.trim(),
    });
    if (r.ok){
      document.getElementById("cfg-key").value = "";
      await loadSettings();
      await loadStatus();
      hint.className = "hint ok";
      hint.textContent = "已保存并启用：" + r.mask;
    } else {
      hint.className = "hint err";
      hint.textContent = r.error || "保存失败";
    }
  } catch(e){
    hint.className = "hint err"; hint.textContent = "保存失败：" + e;
  } finally { btn.disabled = false; }
}
async function testKey(){
  const hint = document.getElementById("cfg-hint");
  hint.className = "hint"; hint.textContent = "正在连接 DeepSeek…";
  try{
    const r = await api("/api/settings/test", {});
    hint.className = "hint " + (r.ok ? "ok" : "err");
    hint.textContent = r.message || (r.ok ? "连接成功" : "连接失败");
    if (r.ok) loadStatus();
  } catch(e){
    hint.className = "hint err"; hint.textContent = "测试失败：" + e;
  }
}
loadAgents(); loadStatus(); loadSettings();
</script>
</body>
</html>
"""  # 页面里的 % 很多（CSS 宽度、URL 编码），所以不用 % 格式化，改 replace 注入版本号

# 品牌署名只留在源码元数据里，页面上不渲染
PAGE = PAGE.replace("__VERSION__", __version__).replace("__BRAND__", "")


class Handler(BaseHTTPRequestHandler):
    server_version = "MerchantAgent/" + __version__

    # ── 基础 ──
    def log_message(self, fmt, *args):  # noqa: A003 - 覆盖父类
        logger.debug("%s - %s", self.address_string(), fmt % args)

    def _send(self, status: int, payload, content_type: str = "application/json"):
        body = payload.encode("utf-8") if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}

    # ── 路由 ──
    def do_GET(self):  # noqa: N802
        path = urlparse(self.path).path
        query = parse_qs(urlparse(self.path).query)
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html")
        if path == "/api/agents":
            return self._send(200, {"agents": ORCH.list_agents()})
        if path == "/api/status":
            return self._send(200, self._status())
        if path == "/api/settings":
            import config
            return self._send(200, config.public_settings())
        if path == "/api/drafts":
            from tools import execute
            return self._send(200, execute("list_drafts", {}))
        if path == "/api/history":
            sid = (query.get("session_id") or [""])[0]
            return self._send(200, {"messages": ORCH.session_messages(sid) if sid else []})
        if path == "/api/doctor":
            import doctor
            _, text = doctor.run_all(verbose=False)
            return self._send(200, {"text": text})
        return self._send(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        path = urlparse(self.path).path
        body = self._body()
        if path == "/api/chat":
            message = (body.get("message") or "").strip()
            if not message:
                return self._send(400, {"error": "message 不能为空"})
            try:
                return self._send(200, ORCH.chat(
                    message, session_id=body.get("session_id") or None,
                    force_route=body.get("route") or ""))
            except Exception as exc:  # noqa: BLE001
                logger.exception("chat 失败")
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        if path == "/api/workflow":
            category = (body.get("category") or "").strip()
            if not category:
                return self._send(400, {"error": "category 不能为空"})
            try:
                result = ORCH.run_workflow(
                    category, budget=body.get("budget", ""), audience=body.get("audience", ""),
                    product=body.get("product", ""), platform=body.get("platform", DEFAULT_PLATFORM))
                return self._send(200, {k: v for k, v in result.items() if k != "steps"})
            except Exception as exc:  # noqa: BLE001
                logger.exception("workflow 失败")
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})
        if path == "/api/settings":
            import config
            try:
                info = config.update_settings(
                    api_key=body.get("api_key"), base_url=body.get("base_url"),
                    model=body.get("model"))
            except ValueError as exc:
                return self._send(400, {"ok": False, "error": str(exc)})
            return self._send(200, {"ok": True, **info})
        if path == "/api/settings/test":
            import llm
            ok, message = llm.check_provider("deepseek")
            return self._send(200, {"ok": ok, "message": message})
        if path == "/api/clear":
            return self._send(200, {"removed": ORCH.clear(body.get("session_id") or "")})
        return self._send(404, {"error": "not found"})

    # ── 状态 ──
    @staticmethod
    def _status() -> dict:
        from config import first_ready_provider, provider_ready, PROVIDERS
        ready = first_ready_provider()
        return {
            "version": __version__,
            "brand": __brand__,
            "backend": CONVERSATION_BACKEND,
            "llm": {
                "ready": bool(ready),
                "provider": ready,
                "label": PROVIDERS[ready]["label"] if ready else "离线规则模式",
            },
            "routes": {k: {"chain": v, "ready": [provider_ready(p) for p, _ in __import__("config").parse_route(v)]}
                       for k, v in AGENT_CHAIN.items()},
            "platforms": {k: v["label"] for k, v in PLATFORMS.items()},
            "usage": store.stats(24),
        }


def _port_in_use(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.6)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def serve(port: int = WEB_PORT, open_browser: bool = True) -> None:
    from config import first_ready_provider, PROVIDERS

    # 端口被占用时直接说清楚，否则 Windows 允许重复绑定，旧进程会继续“吃”请求，
    # 表现就是「改了代码、重启了、刷新了，页面还是老样子」。
    if _port_in_use(port):
        print(f"端口 {port} 已被占用：很可能上一个窗口没关干净。")
        print("处理办法（任选一种）：")
        print(f"  1. 找到标题为 merchant-agent 的窗口关掉，再重新启动")
        print(f"  2. 命令行执行：netstat -ano | findstr :{port}   然后 taskkill /PID <上面最后那列数字> /F")
        print(f"  3. 换个端口启动：python webui.py {port + 1}")
        raise SystemExit(1)

    ready = first_ready_provider()
    if ready:
        print(f"模型：{PROVIDERS[ready]['label']} ✅")
    else:
        print("模型：未配置 ⚠️  将以离线规则模式运行（AI 生成不可用，规则工具仍可用）")
        print("      填 API Key 的方法见 README「三、配置模型」")
    ThreadingHTTPServer.allow_reuse_address = False
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    url = f"http://127.0.0.1:{port}"
    print(f"界面已启动：{url}   （Ctrl+C 退出）")
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    # 参数：允许 `python webui.py 7861 --no-browser`，也允许只给 --no-browser
    port = WEB_PORT
    for arg in sys.argv[1:]:
        if arg.isdigit():
            port = int(arg)
        elif arg != "--no-browser":
            print(f"未知参数：{arg}（可用：端口号 / --no-browser）")
            sys.exit(2)
    serve(port=port, open_browser="--no-browser" not in sys.argv)
