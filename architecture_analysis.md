# Windows Copilot API - 架构分析与实现文档

本项目旨在将 Microsoft Copilot 的底层 WebSocket 协议逆向包装成标准的 OpenAI API 格式，让开发者可以无缝对接到任何兼容 OpenAI 的客户端（如 Chatbox, NextChat 等），并支持云原生的 headless 环境部署。

---

## 1. 核心架构拓扑

项目基于 **FastAPI + Playwright + 纯净 WebSocket** 组合。我们将整个系统打造成了极致的**单端口架构 (Single-Port Architecture)**，极大地简化了部署和反向代理的配置。

### 单端口集成 (Port 8000)
- **API 核心路由**：处理基于 OpenAI 格式的 HTTP 接口。
- **静态文件托管**：内置了管理后台 (`/` index.html) 和 noVNC 前端页面 (`/novnc`)。
- **底层 WebSocket 代理**：FastAPI 提供 `/vnc-ws` 路由，直接通过 Python 的 `asyncio` 与本地 `Xvfb` (端口5900) 建立底层的 TCP 双向通讯。这样无需额外开启 Websockify，彻底消除了外部 6080 端口暴露的需求。

---

## 2. 关键特性与实现原理

### 2.1 云原生 True VNC 登录集成
微软的认证不仅需要正常的 Cookie，还需要通过 Cloudflare 的真人验证（拼图/划块）。在无头模式(Headless)下极易失败。
- **解决思路**：引入 `Xvfb` 创建虚拟显示器 (`DISPLAY=:99`)。并使用 `x11vnc` 将图像抓取至 5900 端口。
- **闭环体验**：当用户在面板触发 `/api/login` 时，Playwright 关闭 headless 模式，在 Xvfb 屏幕内拉起真正的 Chrome。用户直接在前端的管理面板内的 `iframe` （加载了内嵌的 noVNC）与这个无头服务器上的真机发生交互，丝滑地滑块解锁并完成 Token 捕获。

### 2.2 无阻塞并发弹性调度 (Account Pool Fallback)
为了支持团队的高并发场景，打破单个 API Key 与单条连接锁定的僵局。
- **数据结构升级**：实现了 `API Key -> List[Sessions]` 的 1对多映射架构。
- **调度策略**：当大量请求使用 `sk-team` 调用时，`AccountPool` 会在底层维护的多组 Session 实例中进行轮询。
- **非阻塞获取 (Non-Blocking Acquire)**：通过 `lock.acquire(blocking=False)` 寻找第一个未被占用的闲置会话。只要有多账号冗余，系统就不会发生排队，从而实现团队级接口的高吞吐流转。

### 2.3 基于 Hash 的上下文状态机 (Stateful Router)
Copilot 并不接受我们下发的“历史消息数组”，它严格依赖微软后端的 `conversation_id` 进行持续性对话。
- **挑战**：客户端发来的是 `[msg1, msg2, msg3]` 的全新 Payload，怎么知道要不要接上之前的 Copilot 会话？或者如果用户重置/修改了聊天记录怎么办？
- **解决 (MD5 Routing)**：
  1. 对于客户端的 `messages`，我们对其前缀（剔除最后一条消息）进行 MD5 哈希计算 (`history_hash`)。
  2. 我们在内存维护了 `history_hash -> conversation_id` 的状态映射表。
  3. 如果请求的 `history_hash` 命中缓存，我们就提取并复用对应的 `conversation_id` 传给微软。
  4. 只要客户端出现修改历史、重投 (Re-roll) 或新建，哈希链条就会断裂。此时系统会自动 fallback 到向微软申请全新的 `conversation_id`。这样既保证了微软上下文的一致性，又支持了客户端的任意分支漫游。

---

## 3. 开放 API 接口一览

所有 API 都强制校验 Basic Auth 权限（可以通过 `.env` 的 `WEB_AUTH_ENABLED=false` 全局关闭）。

### 3.1 核心 OpenAI 兼容接口
- `GET /v1/models`：兼容列出支持的模型 (`copilot`)。
- `POST /v1/chat/completions`：核心对话接口，全面兼容 `messages`、`stream` 等参数，并使用 HTTP Bearer token 识别对应的 Account API Key。

### 3.2 管理端接口 (Admin API)
- `GET /api/accounts`：列出当前所有的 API Key 及其绑定的 Sessions 列表。
- `POST /api/accounts`：支持传入 JSON `{"api_key": "sk-1", "sessions": ["s1", "s2"]}` 新增绑定映射。
- `POST /api/login`：支持传入 `?session_name=xxx` 参数。接收到后会在后台多线程拉起 Playwright 并绑定至 Xvfb。
- `GET /api/config`：透传后端的配置，将 `VNC_URL` 发送给前端的 iframe，用于解决套了多层反代导致前端无法定位的问题。

### 3.3 隐藏 WebSockets (Internal)
- `WS /vnc-ws`：内部核心枢纽，接管 noVNC 的二进制帧，拆包后与底层的 `127.0.0.1:5900` 完成毫秒级的 TCP 透明转发，完美实现纯 HTTP(s) 下的 VNC 交互。
