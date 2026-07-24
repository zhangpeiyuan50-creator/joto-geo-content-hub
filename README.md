# JOTO GEO Content Hub

JOTO GEO Content Hub 是一套在本机运行的多内容 GEO 工作台，统一管理四条内容生产线：

- FasiumAI
- WorkBuddy x JOTO
- 腾讯云 ADP x JOTO
- Dify x JOTO

系统共享热点采集、Dify Workflow、Unsplash 配图、Job 管理，以及知乎、CSDN、搜狐号发布辅助。内容生成成功后即可进入发布辅助。项目使用 Python 3.10+，网站默认地址为 <http://127.0.0.1:8765/>。

## 重要安全说明

仓库只保存代码和空目录占位文件，不应包含以下本机数据：

- `.env` 和任何真实 API Key
- `outputs/` 中生成的文章与图片
- `data/jobs/`、日志、PID、错误截图
- `data/browser_profile/` 中的平台登录状态

这些内容已加入 `.gitignore`。API Key 请通过公司密码库或私聊单独发送，每位组员只填写自己的 `.env`。如果密钥曾经进入 Git 历史，必须立即撤销并重新生成；只删除文件不能消除历史泄露。

## 组员首次安装

先安装：

- Git
- Python 3.10 或更高版本

克隆私有仓库并进入项目目录：

```powershell
git clone <PRIVATE_REPOSITORY_URL>
cd fasium_geo_auto
```

### Windows

1. 双击 `安装环境.bat`。
2. 安装完成后，打开项目根目录的 `.env`，填写团队单独提供的 Key。
3. 双击 `启动网站.bat`。
4. 浏览器打开 <http://127.0.0.1:8765/>。

安装脚本会自动创建 `.venv`、安装依赖、安装 Playwright Chromium，并在缺少时从 `.env.example` 创建 `.env`。它不会覆盖已有 `.env`。

### macOS / Linux

```bash
bash setup.sh
# 编辑 .env 并填写 Key
bash start.sh
```

首次运行发布辅助时，需要在 Playwright 打开的浏览器中分别登录知乎、CSDN 和搜狐号。登录状态只保存在当前电脑的 `data/browser_profile/`，不会上传 GitHub，也不会与其他组员共享。

## 环境变量

四个 Dify Workflow 必须使用相同输出字段：

```json
{
  "zhihu": "...",
  "csdn": "...",
  "sohu": "...",
  "cover_prompt": "..."
}
```

本机 `.env` 需要填写：

```env
DIFY_API_KEY_FASIUM=
DIFY_API_KEY_WORKBUDDY=
DIFY_API_KEY_ADP=
DIFY_API_KEY_DIFY=
UNSPLASH_ACCESS_KEY=
```

旧变量 `DIFY_API_KEY` 仍可作为 Fasium Workflow 的兼容备用项。Cookie 与 `DIFY_PROXY` 均为可选配置。

## 日常使用

Windows 双击 `启动网站.bat`，macOS/Linux 运行 `bash start.sh`。在网页中可以：

- 按模块手动生成内容
- 查看任务、文章、封面和 metadata
- 查看 WorkBuddy、ADP、Dify 的合作内容与发布状态
- 选择指定 Job 发布到知乎、CSDN 或搜狐号

内容生成成功后即可启动发布辅助，最终发布按钮仍由人工确认。

Windows 也可以双击 `发布知乎.bat`、`发布CSDN.bat` 或 `发布搜狐.bat`。命令行使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe main.py run-once --module fasium
.\.venv\Scripts\python.exe main.py run-once --module workbuddy_joto
.\.venv\Scripts\python.exe main.py run-once --module adp_joto
.\.venv\Scripts\python.exe main.py run-once --module dify_joto
.\.venv\Scripts\python.exe main.py run-once --module all

.\.venv\Scripts\python.exe main.py publish --module adp_joto --platform csdn --job JOB_ID
```

macOS/Linux 将 Python 路径替换为 `.venv/bin/python`。

## 输出结构

```text
outputs/
  fasium/
  workbuddy_joto/
  adp_joto/
  dify_joto/
    job_xxxx/
      zhihu/zhihu_rich.html
      csdn/csdn.md
      sohu/sohu_rich.html
      assets/
        cover_image.jpg
        image_metadata.json
        attribution.txt
        cover_prompt.txt
      metadata.json
```

本地输出不会出现在 Git 变更中。旧的 Fasium 输出仍能在当前电脑展示，但不会分享给组员。

## Scheduler

团队版默认保持手动验证模式，`config.yaml` 中：

```yaml
scheduler:
  enabled: false
```

不要在未经团队确认时开启自动任务。需要守护模式时，Windows 可运行：

```powershell
.\.venv\Scripts\python.exe main.py start
.\.venv\Scripts\python.exe main.py status
.\.venv\Scripts\python.exe main.py stop
```

## 仓库所有者首次发布

先在 GitHub 创建一个空的 Private Repository，再在项目根目录执行：

```powershell
git init
git branch -M main
git add .
git status
```

在提交前确认 `git status` 中没有 `.env`、`outputs/`、`data/browser_profile/`、日志、截图或历史 Job。还可以运行：

```powershell
git check-ignore -v .env
git check-ignore -v outputs/example/metadata.json
git check-ignore -v data/logs/example.log
git check-ignore -v data/browser_profile/example
```

确认后提交并推送：

```powershell
git commit -m "Initial team-ready JOTO GEO Content Hub"
git remote add origin <PRIVATE_REPOSITORY_URL>
git push -u origin main
```

最后在 GitHub 的 `Settings -> Collaborators` 邀请组员。真实 Key 不要写在 Issue、README、Commit 或聊天群公开消息中。

## 更新与排查

拉取代码更新后，如 `requirements.txt` 有变化，可重新运行 `安装环境.bat` 或 `bash setup.sh`。

- 网站打不开：确认启动窗口仍在运行，并访问 <http://127.0.0.1:8765/>。
- 提示缺少环境：先运行安装脚本。
- 提示缺少 Key：检查 `.env` 是否位于 `main.py` 同一目录，并重新启动网站。
- 发布浏览器未安装：重新运行安装脚本，它会执行 Playwright Chromium 安装。
- 端口 8765 被占用：先关闭旧的网站进程，再重新启动。

统一运行日志位于 `data/logs/fasium_geo_auto.log`，发布日志位于 `data/logs/publisher.log`。这些日志仅保存在本机。

## 传播与 GEO 监测

Dashboard 的“传播与 GEO 分析”区域用于监测已发布文章。发布辅助会尝试自动识别发布后的公开 URL；如果平台没有自动跳转，请选择对应 Job，并在“所选 Job 监测”中粘贴知乎、CSDN、搜狐号的公开文章链接。

热度监测包括平台页面能够公开读取的阅读、点赞、评论、收藏、分享或转发数据。平台未提供的指标显示为空，不会当作零。发布后前 30 天每天采集，之后每周采集。

GEO 检测覆盖腾讯元宝、Kimi、DeepSeek、豆包。第一次使用前，在网页中分别点击四个“登录”按钮，完成登录后关闭自动化浏览器窗口。系统不会绕过验证码；登录失效时会在监测待办中提示。

手动运行：

```powershell
.\.venv\Scripts\python.exe main.py monitor engagement
.\.venv\Scripts\python.exe main.py monitor engagement --job-id JOB_ID
.\.venv\Scripts\python.exe main.py monitor geo
.\.venv\Scripts\python.exe main.py monitor geo --job-id JOB_ID
```

模型登录也可以从终端启动：

```powershell
.\.venv\Scripts\python.exe main.py llm-login yuanbao
.\.venv\Scripts\python.exe main.py llm-login kimi
.\.venv\Scripts\python.exe main.py llm-login deepseek
.\.venv\Scripts\python.exe main.py llm-login doubao
```

监测数据保存在 `data/analytics.db`，模型登录状态保存在 `data/llm_profiles/`，GEO 证据截图保存在 `data/logs/geo_screenshots/`。这些内容均已被 `.gitignore` 排除。

`config.yaml` 中 `monitoring.enabled: true` 表示守护进程会执行监测任务；它不等同于 `scheduler.enabled`。因此可以保持内容自动生成关闭，同时让已发布文章继续按计划监测。
