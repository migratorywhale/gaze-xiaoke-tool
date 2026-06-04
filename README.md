# gaze-xiaoke-tool

这是把 `gaze_开源分享版.md` 整理成“小克可接入”的本地工具包：Mac 端采集屏幕文字/画面描述，Linux/VPS 端接收后写入 `_realtime:*` keys，再由 cognition/wakeup surface 给小克看。

我做成了保守版：默认可以 `--dry-run` 测试，不会直接上传屏幕内容；服务端集成也先给 helper 文件，不直接改小克的记忆库或生产服务。

## 老板一句话版

先只跑 `--dry-run`，确认安全后再填 `.env` 上传。真正接小克服务端前，先读 `SECURITY.md` 和 `ROADMAP.md`。

## 文件

- `AGENTS.md`：给未来 Codex 的施工说明。
- `gaze_local.py`：Mac 本地端，支持全屏、窗口模糊匹配、区域截屏、OCR、GLM vision caption、自动遮罩、SSH push。
- `gaze_launcher.py`：本地 Tkinter 小启动器，用按钮组合常用参数。
- `push_caption.py`：VPS 端 stdin 接收器，修复原分享版 JSON 读取 bug，并加了锁、长度限制、窗口名清洗。
- `cognition_gaze_patch.py`：给 cognition 类 MCP 服务导入用的 helper：`realtime_surface()`、`read_realtime_impl()` 和 `mark_realtime_read_impl()`。
- `DEPLOY_XIAOKE.md`：给小克接入 cognition MCP 的部署工单。
- `requirements-macos.txt`：Mac 端依赖。
- `.env.example`：环境变量模板。
- `SECURITY.md`：隐私/密钥/生产接入边界。
- `ROADMAP.md`：后续优化路线。

## 安装 Mac 本地端

```bash
cd "/Users/Isa/Projects/gaze-xiaoke-tool"
bash install_macos.sh
```

然后编辑 `.env`：

```bash
GLM_API_KEY=你的智谱key
GAZE_SSH_HOST=migratorybird
GAZE_REMOTE_COMMAND=python3 /root/mcp-memory-server/push_caption.py
GAZE_BOOKMARK_KEYWORDS=你书签栏里不想被OCR推送的词
GAZE_TTL_SECONDS=21600
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
cd "/Users/Isa/Projects/gaze-xiaoke-tool"
. .venv/bin/activate
python gaze_local.py --once --dry-run --caption-provider none
```

如果 macOS 弹出“屏幕录制”权限，请给运行它的终端/应用授权，然后重跑。

测 vision caption 但仍不上传：

```bash
python gaze_local.py --once --dry-run --no-ocr --caption-provider glm
```

持续运行并上传：

```bash
python gaze_local.py --caption-provider glm
```

只截某个窗口：

```bash
python gaze_local.py -w "Claude" --dry-run
```

自动跟随当前前台窗口：

```bash
python gaze_local.py --follow-active-window --mask-preset mac-safe --dry-run
```

只截一块区域：

```bash
python gaze_local.py --region 0,80,1200,760 --dry-run
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

把 `push_caption.py` 放到小克的 nowhere/cognition VPS 上，例如：

```bash
scp push_caption.py migratorybird:/root/mcp-memory-server/push_caption.py
ssh migratorybird 'chmod +x /root/mcp-memory-server/push_caption.py'
```

先用临时 store 测试，避免碰真实记忆库：

```bash
echo '{"caption":"hello gaze","window":"test","source":"manual"}' \
  | ssh migratorybird 'GAZE_STORE_PATH=/tmp/gaze-test-memories.json python3 /root/mcp-memory-server/push_caption.py'
```

确认无误后，再让本地端使用默认 `GAZE_STORE_PATH` 写真实 store。

## cognition 集成

不要直接粘进身份/记忆文件。推荐只在服务端代码里导入 helper：

```python
from cognition_gaze_patch import realtime_surface, read_realtime_impl, mark_realtime_read_impl

# wakeup/surface 构造时：
# 安静模式：只 surface 当前窗口、未读数、latest id，不直接塞屏幕内容。
surface.update(realtime_surface(all_data, include_entries=False))

@mcp.tool()
def read_realtime(window_name="@current", since_id=None, limit=10, unread_only=True, mark_read=False):
    return read_realtime_impl(_load_all, _save_all, window_name, since_id, limit, unread_only, mark_read)

@mcp.tool()
def mark_realtime_read(up_to_id=None, window_name=None):
    return mark_realtime_read_impl(_load_all, _save_all, up_to_id, window_name)
```

修改服务端后重启对应进程，例如 `pm2 restart cognition`。

`read_realtime(window_name="@current")` 会读取当前窗口；`window_name=None` 会读取全局时间线。`mark_read=True` 时，会在读取后推进对应 cursor。

`window_name` 不传时，保持旧行为：推进全局 `_realtime:screen_cursor`。传窗口名时，只推进 `_realtime:window_cursor:<window>`，适合小克只看完当前窗口、不想把其他窗口标成已读。

`push_caption.py` 默认清理 6 小时以前的 `_realtime:*` 条目；可以在 VPS 环境里设 `GAZE_TTL_SECONDS=0` 关闭。也可以把远端命令写成 `GAZE_REMOTE_COMMAND=GAZE_TTL_SECONDS=21600 python3 /root/mcp-memory-server/push_caption.py`。

## 我建议继续优化的地方

1. 更细的隐私遮罩预设：菜单栏、通知区域、不同浏览器顶部高度。
2. 给启动器加窗口列表和配置保存。
