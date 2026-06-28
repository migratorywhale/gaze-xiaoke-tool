# gaze

这是把 gaze 思路整理成可复用的实时读屏工具包：Mac 端采集屏幕文字/画面描述，Linux/VPS 端接收后写入专用 `gaze_realtime.json` 里的 `_realtime:*` keys，再由独立的 gaze MCP 按需读取。

我做成了保守版：默认可以 `--dry-run` 测试，不会直接上传屏幕内容；服务端的 gaze MCP 和 memory MCP 已经隔离，gaze 不写入长期记忆图谱。

## Acknowledgements

这个仓库的早期形状来自栈（江栈）分享的 gaze 思路；后续也参考了他的开源实现 [jiangxi1129/gaze](https://github.com/jiangxi1129/gaze)，尤其是“窄门 default”、窗口黑名单、OCR 上下文给 vision、mock provider 和失败时不退回全屏这些工程判断。

咱们这版是 macOS + 独立 MCP 管线：截图用 macOS `screencapture`/Quartz，实时数据写入独立 `gaze_realtime.json`，不写进长期记忆库。小克、小G、阿码或其他被授权的 AI 都可以按同一套工具读取临时屏幕摘要。

macOS 安全默认值、独立 gaze MCP 管线、Gemini 接入、窗口截屏 fallback 和
DiscoElysiumBridge 的低 token gaze 适配由小G / 玻璃齿轮（Codex）整理打通。

## 老板一句话版

先只跑 `--dry-run`，确认安全后再上传。AI 读取 gaze 用独立 URL：`https://migratorybird.xyz/mcp/gaze/`。

## 文件

- `AGENTS.md`：给未来 Codex 的施工说明。
- `gaze_local.py`：Mac 本地端，支持全屏、窗口模糊匹配、区域截屏、OCR、GLM vision caption、自动遮罩、SSH push。
- `gaze_launcher.py`：本地 Tkinter 小启动器，用按钮组合常用参数。
- `push_caption.py`：VPS 端 stdin 接收器，修复原分享版 JSON 读取 bug，并加了锁、长度限制、窗口名清洗。
- `cognition_gaze_patch.py`：通用 realtime helper：`realtime_surface()`、`read_realtime_impl()` 和 `mark_realtime_read_impl()`。
- `gaze_mcp_server.py`：独立 gaze MCP server，只暴露 `read_realtime` 和 `mark_realtime_read`。
- `DEPLOY_XIAOKE.md`：小克接入独立 gaze MCP 的部署工单；也可作为其他 AI 接入的参考。
- `requirements-macos.txt`：Mac 端依赖。
- `.env.example`：环境变量模板。
- `SECURITY.md`：隐私/密钥/生产接入边界。
- `ROADMAP.md`：后续优化路线。

## 安装 Mac 本地端

```bash
cd "/Users/Isa/Projects/gaze"
bash install_macos.sh
```

然后编辑 `.env`：

```bash
GLM_API_KEY=你的智谱key
GEMINI_API_KEY=你的 Gemini key
GEMINI_MODEL=gemini-3.5-flash
GAZE_CAPTION_PROVIDER=gemini
GAZE_SSH_HOST=linuxuser@45.76.219.241
GAZE_REMOTE_COMMAND=GAZE_STORE_PATH=/home/linuxuser/search_tool/gaze_realtime.json GAZE_TTL_SECONDS=21600 python3 /home/linuxuser/search_tool/gaze_push_caption.py
GAZE_BOOKMARK_KEYWORDS=你书签栏里不想被OCR推送的词
GAZE_WINDOW_BLACKLIST=你不想被截到的窗口关键词
GAZE_PROMPT_FILE=/path/to/your-gaze-prompt.txt
GAZE_MCP_URL=https://migratorybird.xyz/mcp/gaze/
```

## 本地安全测试

一键安全检查：

```bash
./safe_check.sh
```

打开小启动器：

```bash
. .venv/bin/activate
python gaze_launcher.py
```

如果当前 Python 没有 Tkinter，`gaze_launcher.py` 会自动打开本地网页启动器。也可以直接运行：

```bash
python gaze_launcher.py --web
```

只测截屏和 OCR，不发到 VPS：

```bash
cd "/Users/Isa/Projects/gaze"
. .venv/bin/activate
python gaze_local.py --once --dry-run --caption-provider none
```

密集文字屏可以临时提高 OCR 返回上限，默认是 1200 字符：

```bash
python gaze_local.py --once --dry-run --caption-provider none --max-ocr-chars 2000
```

如果 macOS 弹出“屏幕录制”权限，请给运行它的终端/应用授权，然后重跑。

测 vision caption 但仍不上传：

```bash
python gaze_local.py --once --dry-run --no-ocr --caption-provider gemini
python gaze_local.py --once --dry-run --no-ocr --caption-provider glm
```

没配视觉 key 但想测流程：

```bash
python gaze_local.py --once --dry-run --no-ocr --caption-provider mock
```

自定义 vision prompt：

```bash
python gaze_local.py --caption-provider gemini --prompt-file ./prompts/gaze.txt --dry-run
```

持续运行并上传：

```bash
python gaze_local.py --caption-provider gemini
```

只截某个窗口。默认是窄门：找不到窗口就退出/跳过，不会退回全屏乱截桌面：

```bash
python gaze_local.py -w "Claude" --dry-run
```

如果你确实想回旧行为，显式打开宽门：

```bash
python gaze_local.py -w "Claude" --allow-fullscreen-fallback --dry-run
```

自动跟随当前前台窗口：

```bash
python gaze_local.py --follow-active-window --mask-preset mac-safe --dry-run
```

`--follow-active-window` 检测不到前台窗口时也默认跳过，不会退回全屏。切到邮件、聊天、密码管理器、终端等默认黑名单窗口时会整帧跳过。可以用 `.env` 里的 `GAZE_WINDOW_BLACKLIST` 追加自己的窗口关键词。

只截一块区域：

```bash
python gaze_local.py --region 0,80,1200,760 --dry-run
```

看视频/字幕时，只截窗口里的字幕区域，避免整屏画面和 UI 噪音挤进 OCR/vision：

```bash
python gaze_local.py --follow-active-window --subtitle-roi --dry-run
python gaze_local.py -w "Bilibili" --subtitle-roi lower-third --dry-run
```

`--subtitle-roi` 不带值时等同于 `bottom`，也可以用 `lower-third`、`center`，或写成相对/像素区域：

```bash
python gaze_local.py --subtitle-roi 0,0.55,1,0.35 --dry-run
python gaze_local.py --subtitle-roi 0%,55%,100%,35% --dry-run
python gaze_local.py --subtitle-roi 0,600,1920,360 --dry-run
```

懒人视频模式会自动启用：前台窗口跟随（除非你指定了 `--window` / `--region`）、浏览器顶部自动遮罩、字幕 ROI：

```bash
python gaze_local.py --video-mode --dry-run
python gaze_local.py --video-mode --caption-provider gemini
```

遮掉常见隐私区域后再识别：

```bash
python gaze_local.py --mask-preset mac-safe --dry-run
python gaze_local.py --mask-preset browser-top --mask-preset dock-bottom --dry-run
python gaze_local.py --mask-rect 0,0,1200,140 --dry-run
```

根据当前 app 自动加隐私遮罩：

```bash
python gaze_local.py --follow-active-window --auto-mask --dry-run
```

减少重复 vision 调用：

```bash
python gaze_local.py --vision-min-diff 5 --dry-run
```

批量推送设置：

```bash
python gaze_local.py --batch-interval 3 --max-batch 10
```

## VPS 接入

完整小白工单见 `DEPLOY_XIAOKE.md`。下面是核心命令速记。

我们的 MCP 服务器是 `linuxuser@45.76.219.241`，服务目录是 `/home/linuxuser/search_tool`。

把接收器和 helper 放到服务器上，例如：

```bash
scp push_caption.py linuxuser@45.76.219.241:/home/linuxuser/search_tool/gaze_push_caption.py
scp cognition_gaze_patch.py linuxuser@45.76.219.241:/home/linuxuser/search_tool/gaze_realtime_tools.py
ssh linuxuser@45.76.219.241 'chmod +x /home/linuxuser/search_tool/gaze_push_caption.py'
```

我们的 memory MCP 使用 JSONL 图谱记忆，所以 gaze 实时数据必须放在单独 JSON 文件里，不能写进 `/home/linuxuser/.mcp/memory.jsonl`。先用临时 store 测试：

```bash
echo '{"caption":"hello gaze","window":"test","source":"manual"}' \
  | ssh linuxuser@45.76.219.241 'GAZE_STORE_PATH=/tmp/gaze-test-memories.json python3 /home/linuxuser/search_tool/gaze_push_caption.py'
```

确认无误后，再让本地端使用 `GAZE_STORE_PATH=/home/linuxuser/search_tool/gaze_realtime.json` 写正式实时 store。

## 独立 MCP 集成

不要直接粘进身份/记忆文件，也不要把 gaze 工具挂进 memory MCP。已部署版本是独立服务：

```text
public URL: https://migratorybird.xyz/mcp/gaze/
local port: 127.0.0.1:8772
process: pm2 gaze-mcp
store: /home/linuxuser/search_tool/gaze_realtime.json
```

它只暴露：

- `read_realtime`
- `mark_realtime_read`

`read_realtime(window_name="@current")` 会读取当前窗口；`window_name=None` 会读取全局时间线。`mark_read=True` 时，会在读取后推进对应 cursor。

`window_name` 不传时，保持旧行为：推进全局 `_realtime:screen_cursor`。传窗口名时，只推进 `_realtime:window_cursor:<window>`，适合只看完当前窗口、不想把其他窗口标成已读。

`gaze_push_caption.py` 默认清理 6 小时以前的 `_realtime:*` 条目；可以在 VPS 环境里设 `GAZE_TTL_SECONDS=0` 关闭。完整连接信息含 token 保存在本地私密文件 `.gaze_mcp_connection.txt`，不会提交到 GitHub。

## 我建议继续优化的地方

1. 更细的隐私遮罩预设：菜单栏、通知区域、不同浏览器顶部高度。
2. 给启动器加窗口列表和配置保存。
