# Microsoft Copilot Enterprise (E5) API 接入与登录使用指引

本指引将详细介绍如何使用本项目将您的 **Microsoft Copilot Enterprise (E5 / Business Chat)** 企业级账户转化为本地兼容 OpenAI 规范的 API 服务。

---

## 📋 核心前提要求

1. **企业版账户**：您必须拥有一个已启用 **Microsoft Copilot Enterprise (E5) / Business Chat** 授权的工作或学校账户（Work or School Account / Microsoft Entra ID）。
2. **本地环境**：
   - Python 3.9+ 运行环境。
   - 网络能够稳定访问微软 Copilot 服务（建议具备稳定且非公用数据中心的美国代理节点，以确保美国区伪装顺畅）。

---

## 🚀 步骤 1：本地环境准备与依赖安装

在项目根目录下打开命令行工具（如 PowerShell / cmd / Bash）：

### 1. 创建并激活虚拟环境

* **Windows (PowerShell)**:
  ```powershell
  python -m venv venv
  venv\Scripts\Activate.ps1
  ```
  *(如果提示权限受限，可先运行：`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`)*

* **macOS / Linux**:
  ```bash
  python3 -m venv venv
  source venv/bin/activate
  ```

### 2. 安装 Python 依赖包
```bash
pip install -r requirements.txt
```

### 3. 安装 Playwright 浏览器内核 (仅需一次)
```bash
playwright install chromium
```

---

## 🔑 步骤 2：授权登录与会话初始化 (Login)

项目内置了 Playwright 驱动的自动化登录与会话保存机制（Session Minter）。它会将 Cookie 与会话令牌加密缓存在本地 `session/` 目录中，在后续运行 API 时自动维持心跳与刷新，无需重复登录。

### 1. 执行登录指令
在激活的虚拟环境中运行：
```bash
python -m copilot login
```

### 2. 交互登录过程
1. 系统会自动弹出一个 **Chromium 浏览器窗口**，并导航至微软的官方 Copilot 登录页面。
2. **关键动作**：请在弹出的浏览器中输入您的 **Microsoft E5 / 工作或学校组织账户** 并完成登录（包括您组织可能要求的双重身份验证 / MFA）。
3. **安全校验**：如果页面弹出 Cloudflare 的 “Verify you're human” 验证码或人机交互复选框，请在浏览器中**手动点击勾选**。
4. **自动完成**：登录成功并加载 Copilot 主界面后，底层的 Playwright 脚本会自动拦截捕获安全凭证（Token 与 Cookies），并在后台发送一条简短的“测试热身消息”。
5. 热身成功后，**浏览器窗口会自动关闭**。此时本地命令行会提示 `Setup complete!`。

> [!IMPORTANT]
> **全链路美国区环境伪装 (US Locale Spoofing)**
> 为了防止微软的安全合规遥测拦截非美区账户或弹出区域不可用，系统在打开浏览器及发送请求时，已全面强行伪装为 **en-US** 区域和 **America/New_York**（东部时间）时区。在登录浏览器中请勿手动更改语言设置。

---

## 🔌 步骤 3：启动本地 OpenAI 兼容服务端 (Enable)

登录成功后，即可脱离浏览器，在后台直接启动 FastAPI 网关：

```bash
python -m server
```
或者使用 `uvicorn` 精细化启动：
```bash
venv\Scripts\python.exe -m uvicorn server.api:app --host 127.0.0.1 --port 8000
```

服务启动后，将在本地建立以下兼容 OpenAI 协议的端点：
* **聊天完成接口**: `POST http://127.0.0.1:8000/v1/chat/completions`
* **模型列表接口**: `GET http://127.0.0.1:8000/v1/models`

### 可配置的环境变量
启动命令前可注入以下环境变量进行限流与端口微调：
- `PORT`: 服务端口（默认 `8000`）。
- `HOST`: 监听地址（默认 `127.0.0.1`；若需容器/局域网访问可设为 `0.0.0.0`）。
- `RATE_LIMIT_RPM`: 每分钟最大请求数限制（默认 `12` 次/分钟，设为 `0` 可禁用）。
- `RATE_LIMIT_BURST`: 突发并发数上限（默认 `4`）。

---

## 🎨 步骤 4：核心模型选择与功能路由

我们在外部 OpenAI 的 `model` 参数与 E5 后台之间构建了**动态模型注册路由**。您可在第三方客户端（例如 NextChat, LobeChat, Dify, Cursor 等）的 `model` 配置框中直接传入以下字符：

### 1. 原生核心三大模式

| 外部模型参数 (`model`) | 路由映射机理 | E5 底层特征 | 适用场景 |
| :--- | :--- | :--- | :--- |
| **`auto`** 或 **`自动`** / **`copilot`** | 对应 **自动** 模式 | `mode: "Magic"` | 默认路由。自动权衡上下文长度与思考深度。 |
| **`fast`** 或 **`快速答复`** / **`gpt-5.5-fast`** | 对应 **快速答复** 模式 | `mode: "Chat"` / `mode: "Gpt_5_5_Chat"` | 适合日常闲聊、简单问答，响应时间极短。 |
| **`thinking`** 或 **`深度思考`** / **`gpt-5.5`** | 对应 **深度思考** 模式 | `mode: "Reasoning"` / `mode: "Gpt_5_5_Reasoning"` | 开启推理链。对于逻辑、编程等复杂任务，将思考更长时间。 |

### 2. 扩展合作模型 (Anthropic Claude)
* **模型参数**：`anthropic` 或 `claude` / `claude-opus-4.8` / `claude-fable-5` / `claude-mythos-5`
* **功能**：通过 E5 Enterprise 独有的 GPT-Agent 接口，直接调取已集成在您 E5 工作区中的 **Anthropic Claude 旗舰模型** 进行对话。

### 3. Microsoft 365 专业助理智能体
* **模型参数**：`word`、`excel`、`powerpoint`、`designer`、`pages`
* **功能**：自动调取对应的 Office Graph 专业助手。其中：
  - `designer` 智能体支持生成高质量画作。
  - 接口层已完美支持流式 `imageGenerated` 多模态转换。当触发画图时，接口会将生成的图像链接转为标准 Markdown 图片格式（如 `![image](url)`）流式推送到您的前端渲染展示。

---

## 💻 步骤 5：第三方客户端配置示例

### 1. NextChat (ChatGPT-Next-Web) 接入配置
1. 打开 NextChat 设置界面。
2. **接口地址 (Base URL)**: `http://127.0.0.1:8000` (或 `http://127.0.0.1:8000/v1`)
3. **API Key**: 填入任意字符即可（如 `unused`，不能为空）。
4. **自定义模型**: 填入 `-all, +auto, +fast, +thinking, +anthropic, +designer`。
5. 保存即可在聊天框顶部自由切换模式。

### 2. Python OpenAI SDK 代码示例
```python
from openai import OpenAI

client = OpenAI(
    base_url="http://127.0.0.1:8000/v1", 
    api_key="unused"
)

# 发送深度思考（Reasoning）模型请求
response = client.chat.completions.create(
    model="thinking",
    messages=[{"role": "user", "content": "用 Python 写一个红黑树的插入算法。"}],
    stream=True
)

for chunk in response:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)
```

---

## 🔍 异常排查与诊断 (Troubleshooting)

如果您在登录或调用过程中遇到验证码卡死、API 返回 503 (Clearance Required)、502 (Bad Gateway) 等情况，可运行附带的自检诊断程序：

```bash
python tests/diagnostic.py
```

- **诊断机理**：该程序会自动在一个可视化的 Chromium 浏览器中打开 Copilot 会话，要求您发送一次消息。
- **作用**：通过这步操作，它能自动通过 Cloudflare 盾并重新捕获最新的安全指纹。
- **输出**：在 `session/diagnostic_report.txt` 下会生成一份脱敏的诊断报告，能帮助快速定位网络、Cookie 或 Token 过期问题。
您可以指定会话：`python tests/diagnostic.py --session session_name`，如果在无图形界面的 Linux 服务器上，可以使用 `--vnc` 参数拉起内置的 VNC 服务。

---

## 🐳 Docker 部署与 Web VNC (Headless)

如果您在服务器（无显卡/无桌面）环境中运行，建议使用 Docker 进行部署。我们提供了云原生虚拟桌面（Xvfb + x11vnc + noVNC）的无缝集成方案。

1. **一键启动容器**
   ```bash
   docker-compose up -d
   ```
   这会自动构建镜像，并暴露出 8000（API与管理面板）和 6080（VNC桌面）端口。

2. **Web 管理面板与可视化登录**
   打开浏览器访问 `http://<服务器IP>:8000`。
   在这里，您可以：
   - 看到目前所有的 API Keys 和映射的会话 (Sessions)。
   - 新增 API Key 及其绑定的多个 Session。
   - **一键 VNC 登录**：点击左侧的某个 Session 标签，右侧会自动内嵌加载出 noVNC 画布。此时，容器内会利用虚拟屏幕拉起一个真正的 Chrome，您可以像操作本地电脑一样在网页中滑动鼠标、输入密码来完成 Microsoft 的验证。

3. **并发与弹性回退 (Account Pool Fallback)**
   在 Web 界面中，您可以给一个 API Key（比如 `sk-team`）绑定多个 Session（比如 `e5-acc1, e5-acc2`）。
   当多用户同时使用 `sk-team` 发送请求时，系统会自动在底层的 `e5-acc1` 和 `e5-acc2` 之间实现**非阻塞的并发调度**。只要有空闲的账号，请求就会无缝接管，极大提升了团队共享场景下的吞吐量！

4. **Linux 命令行手动操作**
   如果你不在 Docker 中，但在一台无界面的 Linux 终端，也可以主动触发 VNC 模式：
   ```bash
   python -m copilot login my-session --vnc
   ```
   系统会拉起 DISPLAY=:99，然后你可以通过本地的 VNC 客户端进行查看。
