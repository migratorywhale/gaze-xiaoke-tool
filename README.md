# gaze_xiaoke_tool

这是把 `gaze_开源分享版.md` 整理成“小克可接入”的本地工具包：Mac 端采集屏幕文字/画面描述，Linux/VPS 端接收后写入 `_realtime:*` keys，再由 cognition/wakeup surface 给小克看。

我做成了保守版：默认可以 `--dry-run` 测试，不会直接上传屏幕内容；服务端集成也先给 helper 文件，不直接改小克的记忆库或生产服务。

## 老板一句话版

先只跑 `--dry-run`，确认安全后再填 `.env` 上传。真正接小克服务端前，先读 `SECURITY.md` 和 `ROADMAP.md`。

## 文件

- `AGENTS.md`：给未来 Codex 的施工说明。
- `gaze_local.py`：Mac 本地端，支持全屏、窗口模糊匹配、区域截屏、OCR、GLM vision caption、SSH push。
- `push_caption.py`：VPS 端 stdin 接收器，修复原分享版 JSON 读取 bug，并加了锁、长度限制、窗口名清洗。
- `cognition_gaze_patch.py`：给 cognition 类 MCP 服务导入用的 helper：`realtime_surface()` 和 `mark_realtime_read_impl()`。
- `requirements-macos.txt`：Mac 端依赖。
- `.env.example`：环境变量模板。
- `SECURITY.md`：隐私/密钥/生产接入边界。
- `ROADMAP.md`：后续优化路线。

## 安装 Mac 本地端

```bash
cd gaze_xiaoke_tool
bash install_macos.sh
```

然后编辑 `.env`：

```bash
GLM_API_KEY=你的智谱key
GAZE_SSH_HOST=migratorybird
GAZE_REMOTE_COMMAND=python3 /root/mcp-memory-server/push_caption.py
GAZE_BOOKMARK_KEYWORDS=你书签栏里不想被OCR推送的词
```

## 本地安全测试

一键安全检查：

```bash
./safe_check.sh
```

只测截屏和 OCR，不发到 VPS：

```bash
cd gaze_xiaoke_tool
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

减少重复 vision 调用：

```bash
python gaze_local.py --vision-min-diff 5 --dry-run
```

批量推送设置：

```bash
python gaze_local.py --batch-interval 3 --max-batch 10
```

## VPS 接入

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
from cognition_gaze_patch import realtime_surface, mark_realtime_read_impl

# wakeup/surface 构造时：
surface.update(realtime_surface(all_data))

@mcp.tool()
def mark_realtime_read(up_to_id=None):
    return mark_realtime_read_impl(_load_all, _save_all, up_to_id)
```

修改服务端后重启对应进程，例如 `pm2 restart cognition`。

## 我建议继续优化的地方

1. MCP 读工具而非 wakeup 灌入：wakeup 只给“有未读 N 条”，小克想看时再拉取，避免每次上下文被屏幕流污染。
2. per-window cursor：现在只有全局 `_realtime:screen_cursor`，切窗口后可能互相压掉已读状态。
3. TTL 清理：`_realtime:*` 是瞬时感知，不应该长期进记忆库；服务端可以定期丢弃超过几小时的 entries。
