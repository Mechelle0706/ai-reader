# AI 阅读器（ai-reader）

本项目按产品需求与开发执行文档 v1.1 分单位搭建。当前提供本机入口页、公众号和 PDF 处理、内容阅读页；小红书和 B 站等待 U8 浏览器方案。

## 环境要求

- Python 3.11 或更高版本
- Windows PowerShell（以下命令以 Windows 为例）

## 安装

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Playwright 使用本机已安装的 Microsoft Edge（Chromium 内核），无需下载独立浏览器内核。Edge 应安装在本机并可正常启动。

如系统没有 `py` 启动器，可用 `python -m venv .venv` 创建虚拟环境。

## 启动

```powershell
.\.venv\Scripts\Activate.ps1
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

服务仅监听本机。访问 <http://127.0.0.1:8000/> 打开入口页，可提交公众号链接或上传 PDF；U4–U6 的模块会依次完成分发、清洗和发布，并返回本机 `/read/{slug}` 链接。`/health` 返回 `ai-reader running`。按 `Ctrl+C` 停止服务。

公众号抓取会启动有界面的 Edge。请在已登录的 Windows 桌面会话中以前台 PowerShell 运行 Uvicorn，并保持终端打开；不要用隐藏窗口或服务账户将它作为后台服务运行。按 `Ctrl+C` 正常停止时，程序会关闭 Playwright 上下文并释放 `data/browser_profile/`。

Playwright 浏览器会通过 `channel="msedge"` 启动 Edge。持久上下文使用 `data/browser_profile/` 保存项目专用登录态；关闭浏览器上下文不会删除该目录，后续启动可复用登录态。不要让多个进程同时以该目录启动 Edge。

## 本地数据与凭证

- 本地环境变量配置放在 `.env`，请勿提交到版本库；`.env.example` 不含真实凭证。
- U3 当前通过 `.env` 中的 `BILIBILI_SESSDATA` 读取 B 站登录态。请只在本机填写，不要把值粘贴到聊天、日志或 Git 提交中。
- SQLite 数据库和 Playwright 登录资料位于 `data/`，均为本地敏感数据，不要共享或提交。
- 已发布内容可在本机访问 `http://127.0.0.1:8000/read/{slug}`；响应直接包含标题和 Markdown 正文。
- 删除单条已发布内容：在项目虚拟环境中运行 `python -c "from app.publisher import delete_document; print(delete_document('你的slug'))"`。删除后该链接返回 404。
- 删除本地数据时，先停止服务，再删除 `data/` 中对应文件/目录。

## 项目边界

第一期仅供本机访问，不配置公网隧道或远程访问。抓取仅针对用户有权访问的内容；不绕过付费墙、验证码、人机验证或平台风控。详见产品需求与开发执行文档。
