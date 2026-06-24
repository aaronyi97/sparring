# 对练场部署指南（v1 · 只写不跑）

> ⚠️ 本目录脚本 v1 阶段**只写不跑**——实际部署涉及外部服务与上线，
> 需自行配置后再执行。以下为参考链路。

## 架构

```
浏览器 ──→ 静态前端（web/dist · Cloudflare Pages 或任意静态托管）
              │ /api/* 反代
              ▼
         FastAPI（uvicorn 单 worker · 阿里云轻量服务器）
              │
         SQLite WAL（data/ · sessions.db + sparring.db）
              │
         OpenAI 兼容中转 / Moonshot / DeepSeek 官方（模型）
```

## 为什么单 worker

后端用 SQLite WAL + 进程内 `_synth_tasks`（后台综合任务注册表），多 worker 会各持一份
内存态导致 reveal 打到别的 worker 拿不到后台综合。v1 单 worker 足够（0→小用户量）。
要扩容：把会话存储换 Postgres + 综合任务下沉到队列（v2 议题）。

## 步骤（授权后执行）

1. 前端构建：`cd web && npm ci && npm run build` → 产物 `web/dist/`
2. 后端依赖：`python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt`
3. 配置 `.env`（参考 .env.example 八路凭证）+ `config.yaml`（模型路由）
4. 起服务：`deploy/run-prod.sh`（uvicorn 单 worker）或 systemd 挂 `deploy/sparring.service`
5. 反代：前端 `/api/*` 指向后端；后端只监听本地，由反代/Tunnel 暴露
6. 种子邀请码：`.venv/bin/python -m sparring.seed_invite 10 "首批种子"`

## 健康检查

`curl -sf http://127.0.0.1:8788/api/health` → `{"ok":true}`
