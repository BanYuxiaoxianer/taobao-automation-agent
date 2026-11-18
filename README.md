# 商家智能运营 Agent · 

> 一个跑在自己电脑上的小店运营助手：**选品 → 写标题卖点 → 合规自查 → 客服话术 → 运营建议**。
> 只依赖 `httpx` + `rich` 两个包，没有 Docker、没有数据库服务、没有 GPU 也能启动。

本版基于开源项目 [yyf2002-oos/merchant-agent](https://github.com/yyf2002-oos/merchant-agent) 的**思路**重写，
代码结构、模块划分与实现全部重做，去掉了对 Ollama / MySQL / Milvus / Gradio 的硬依赖，
并补齐了上游缺失的几块能力。

---

## 一、三分钟跑起来

**最省事：双击 `启动.bat`。** 它会自动建环境、装依赖、生成示例数据、开网页（只第一次慢，之后几秒）。

手动方式：

```powershell
# 1. 进目录
cd D:\花梨鼠标\merchant-agent

# 2. 建环境（只需一次）
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 3. 生成示例知识库（商品/FAQ/供应商/模板，之后随便改）
python seed.py

# 4. 自检：告诉你缺什么、怎么补
python doctor.py

# 5a. 开网页（推荐）
python webui.py            # 浏览器自动打开 http://127.0.0.1:7860

# 5b. 或者用命令行
python cli.py              # 交互菜单
python cli.py ask "买家问什么时候发货，怎么回"
```

**没有 API Key 也能启动**：会自动进入「离线规则模式」——
标题模板、合规检查、利润计算、草稿清单、客服知识库检索这些**不花 token 的功能照常可用**，
只有自由撰写的文案会退化成模板。填一个 Key 就满血。

---

## 二、它能干什么

| 场景 | 入口 | 输出 |
|---|---|---|
| 判断一个品类能不能做 | 选品官 / `cli.py agent selector` | 关键词评分表、货源成本、定价建议 |
| 写标题、卖点、详情 | 上架师 / `cli.py lister "商品名"` | 3 个平台标题 + FAB 卖点 + 详情三段式 + 关键词 |
| 回复买家消息 | 客服官 | 基于**你自己**的 FAQ 和商品库作答，查不到就明确说转人工 |
| 算利润、定价格 | 运营参谋 | 毛利、毛利率、目标毛利倒推售价 |
| 文案合规自查 | 合规审核员 | 广告法绝对化用语 + 平台违禁词 + 标题长度 |
| 全流程走一遍 | `cli.py workflow 家居日用` | 一份 Markdown 报告，自动落 `outputs/` |

---

## 三、配置模型

**最省事的办法：在网页里直接填。** 打开页面后，右侧栏最上面就是「模型设置（DeepSeek）」，
把 Key 粘进去点「保存并启用」，右下角小字会显示 `当前生效：sk-ab…1234`。保存后立刻生效，
不用重启、不用改文件。点「测试连接」会真发一次请求验证 Key 能不能用。

Key 存在 `data/settings.json`，只在本机、只回显掩码（前 5 位 + 后 4 位），原 Key 不会出现在网页上。
想换 Key 直接重新粘贴；想删掉就删 `data/settings.json` 并重启。

也可以用配置文件（适合不想在网页输入的场合）：复制 `.env.example` 为 `.env`（同在项目根目录），按需填一种即可：
复制 `.env.example` 为 `.env`（同在项目根目录），按需填一种即可：

```ini
# 方案 A：DeepSeek（最省事，国内直连）
DEEPSEEK_API_KEY=sk-xxxxxxxx

# 方案 B：任意 OpenAI 兼容服务（硅基流动 / 通义 / 智谱 / LM Studio / vLLM）
# LLM_PROFILE=compat
# COMPAT_API_KEY=sk-xxxxxxxx
# COMPAT_API_BASE=https://api.siliconflow.cn
# COMPAT_MODEL=Qwen/Qwen2.5-7B-Instruct

# 方案 C：本机 Ollama（需先 `ollama serve` 并 pull 模型）
# LLM_PROFILE=local
# OLLAMA_MODEL=qwen2:7b
```

模型链支持**逗号分隔的兜底**：`LLM_CHAIN=deepseek:deepseek-chat,compat:Qwen/Qwen2.5-7B-Instruct`，
主模型挂了自动切下一个。每个 Agent 也可以单独指定模型（`SELECTOR_MODEL` / `LISTER_MODEL` …）。

> 不填 Key、也不装 Ollama 时，程序**不会崩**，只是 AI 生成部分走离线模板。

---

## 四、换成你自己的数据

数据都在 `data/knowledge/` 下，纯 JSON，用记事本改就行：

| 文件 | 内容 | 谁在用 |
|---|---|---|
| `products.json` | 商品名称、品类、参数、成本价、卖点、关键词 | 上架师、客服、选品 |
| `faq.json` | 常见问答（`q` / `a`） | 客服官 |
| `suppliers.json` | 供应商、批发价、起订量、产地 | 选品与成本核算 |
| `templates.json` | 各品类标题范式、详情骨架、类目路径 | 上架师 |

草稿清单在 `data/drafts/drafts.json`，可以 `python cli.py export` 导成 CSV（UTF-8 BOM，Excel 直接打开不乱码）。
对话与调用记录在 `data/merchant.db`（SQLite），删掉就是清空重来。

---

## 五、相比上游改了什么

**去掉的重依赖**

| 上游 | 本版 | 原因 |
|---|---|---|
| Ollama + qwen2:7b（约 5GB） | 默认不用，可选 | 大多数人的电脑跑不动，且首启体验极差 |
| MySQL 8 + Docker（端口 3307） | SQLite 单文件 | 装 Docker 只为存聊天记录不值 |
| Milvus 向量库（端口 19530） | JSON 向量缓存（可选） | 商品库几百条时向量库是纯负担 |
| Gradio 5 十个 Tab（约 300MB） | 标准库单页 + REST 接口 | 装不上 Gradio 就什么都用不了 |
| 硬编码账号 `admin/admin123` | 本机单用户，无登录 | 本机工具不需要假登录，反而误导入公网就是漏洞 |

**新增的能力**

1. **离线规则模式**：模型不可用时自动降级到规则剧本，界面照常出结果，而不是一片报错
2. **合规审核员**：广告法绝对化用语（第一/最好/100%…）+ 平台违禁词 + 标题长度，本地静态规则，零成本
3. **待上架清单**：草稿 → 就绪 → 已上架的状态流转，可导出 CSV 给人工上传
4. **模型链兜底**：`主模型,备模型` 写法和逐 Agent 覆盖，主模型 429/超时不再整轮失败
5. **成本可见**：每次调用记录 token、耗时、估算费用，网页右侧可看今日用量
6. **会话自动压缩**：对话超过 16 条自动压成摘要，长聊不会越聊越贵

---

## 六、合规说明（务必读一下）

本项目**不做**下面这些事，也不建议你去做：

- ❌ 模拟点击千牛 / 淘宝后台（违反平台协议，账号有封禁风险）
- ❌ 调用任何非公开、非官方的「内部接口」
- ❌ 保存你的平台账号密码
- ❌ 自动登录、自动发消息、自动上架

它做的是：**把素材写好、把风险查出来、把清单整理好，最后一步由你亲手点**。
真要接官方能力，走淘宝开放平台（`taobao.open` 系列 API）+ 企业资质申请千牛插件，这是唯一合规路径。
`tools/marketing` 里的「链接拼接」只生成官方搜索页 URL，不抓取任何数据。

---

## 七、目录结构

```
merchant-agent/
├── config.py            配置与模型路由（.env 解析、平台规则表）
├── llm.py               OpenAI 兼容 / Ollama 双协议调用层
├── store.py             SQLite 持久化（对话、摘要、调用监控、草稿）
├── tools/__init__.py    16 个工具：知识库 / 草稿 / 合规 / 定价 / 链接 / 备注
├── core/agent.py        ReAct 内核 + 离线兜底剧本
├── agents/__init__.py   六个 Agent 的人设、工具集、提示词
├── orchestrator.py      意图路由、会话记忆、一键工作流
├── doctor.py            启动自检（缺什么、怎么补）
├── seed.py              示例知识库生成
├── cli.py               命令行 / 交互菜单
├── webui.py             网页界面（标准库 HTTP 服务，无框架依赖）
├── tests/               单元测试与冒烟测试
├── data/                运行时数据（知识库、草稿、数据库）
└── outputs/             导出的 CSV / Markdown 报告
```

接口一览（网页界面就是调这些）：

```
GET  /api/agents     列出 Agent
GET  /api/status     模型状态、路由、今日用量
GET  /api/drafts     待上架清单
POST /api/chat       {"message": "...", "route": "", "session_id": null}
POST /api/workflow   {"category": "家居日用", "platform": "taobao"}
POST /api/clear      {"session_id": "..."}
```

---

## 八、常见问题

**Q：启动就提示「离线规则模式」？**
A：没找到可用模型。要么填 `DEEPSEEK_API_KEY`，要么 `LLM_PROFILE=local` 并确保 Ollama 在跑。自检里会告诉你哪一个没通。

**Q：模型返回乱码或报 401？**
A：Key 无效或 Base URL 少了 `/v1`。用 `python -m doctor` 看模型段的路由与标记。

**Q：中文在 PowerShell 里乱码？**
A：程序内部已强制 UTF-8 输出，如果仍乱码，先在终端执行 `chcp 65001`。

**Q：改了代码、重启了、也刷新了，页面还是老样子？**
A：八成是旧进程还占着 7860（Windows 允许同一端口被重复绑定，请求会被最早那个进程接走）。
两种解法：①双击 **`重启.bat`**（先杀掉占用端口的旧进程再启动）；②双击 `启动.bat`，它会检测到
端口被占用并问你「结束旧进程并继续？」（8 秒不选默认结束）。确认办法：
`netstat -ano | findstr :7860` 只应出现一条 LISTENING；浏览器记得 **Ctrl+F5** 强刷。

**Q：改颜色没变化？**
A：先确认改的是正在运行的那一份（桌面副本与 D 盘是两份独立文件），再确认新旧颜色差异够大 ——
`#f5f6f8` → `#faf8f5` 只差几个色阶，肉眼看不出来。

**Q：同时开好几个窗口会怎样？**
A：第二个窗口会被拒绝启动并提示端口占用。别硬开，用 `重启.bat`。

**Q：想换 MySQL？**
A：`store.py` 的接口是固定的，`CONVERSATION_BACKEND=mysql` 已留好开关位，按需接自己的实现。

---

## 九、许可

MIT License © 2026 31 · 见 [LICENSE](LICENSE)

水印只保留在源码注释、`package` 元数据与本文档中，**界面与对话正文里不显示任何作者信息**。
