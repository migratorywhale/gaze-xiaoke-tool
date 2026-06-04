# 给小克接入 MCP 的部署工单

## 先讲人话

这个工具不是一个新的独立 MCP server。它更像给小克现有的 cognition/nowhere MCP 服务加一只“实时读屏抽屉”：

```text
Isa 的 Mac
  gaze_local.py 截屏/OCR/描述
      |
      | ssh
      v
小克的 VPS
  push_caption.py 接收 JSON
      |
      v
  memories.json 里的 _realtime:* keys
      |
      v
  cognition MCP 暴露 read_realtime / mark_realtime_read
      |
      v
小克按需读取
```

所以要做的是三件事：

1. 把 `push_caption.py` 放到 VPS，负责接收 Mac 推过去的内容。
2. 把 `cognition_gaze_patch.py` 放到 cognition MCP 代码目录，给现有服务导入。
3. 在现有 MCP 服务里注册 `read_realtime` 和 `mark_realtime_read` 两个工具。

## 安全边界

- 先用 `/tmp/gaze-test-memories.json` 测试接收器，不碰真实记忆库。
- 不把这段代码粘进身份、日记、记忆正文之类的文件。
- wakeup/surface 默认只放未读数、当前窗口、latest id；内容由小克调用 `read_realtime` 时再拉。
- `push_caption.py` 默认只写 `_realtime:*` keys，并默认清理 6 小时以前的实时条目。
- `.env` 留在本地，不能提交到 GitHub。

## 0. 本地准备

在 Mac 端确认工具包和安全检查都正常：

```bash
cd "/Users/Isa/Projects/gaze-xiaoke-tool"
. .venv/bin/activate
./safe_check.sh
```

下面命令假设小克 VPS 的 SSH alias 是 `migratorybird`，服务目录是 `/root/mcp-memory-server`。如果实际目录不同，只改这两个变量：

```bash
export GAZE_REMOTE_HOST=migratorybird
export GAZE_REMOTE_DIR=/root/mcp-memory-server
export GAZE_TEST_STORE=/tmp/gaze-test-memories.json
```

## 1. 上传接收器和 helper

```bash
cd "/Users/Isa/Projects/gaze-xiaoke-tool"
scp push_caption.py cognition_gaze_patch.py "$GAZE_REMOTE_HOST:$GAZE_REMOTE_DIR/"
ssh "$GAZE_REMOTE_HOST" "chmod +x $GAZE_REMOTE_DIR/push_caption.py"
```

## 2. 先测临时 store

这一步只写 `/tmp/gaze-test-memories.json`：

```bash
printf '%s\n' '{"caption":"hello gaze from deploy test","window":"deploy-test","source":"manual"}' \
  | ssh "$GAZE_REMOTE_HOST" "GAZE_STORE_PATH=$GAZE_TEST_STORE python3 $GAZE_REMOTE_DIR/push_caption.py"

ssh "$GAZE_REMOTE_HOST" "python3 -m json.tool $GAZE_TEST_STORE | sed -n '1,120p'"
```

期望看到类似：

```text
OK count=1 ids=... timeline=1 window=deploy-test
```

并且 JSON 里出现 `_realtime:screen_caption`、`_realtime:window:deploy-test`、`_realtime:current_window`。

## 3. 接入 cognition MCP 服务

在小克现有的 cognition MCP 服务代码里加导入：

```python
from cognition_gaze_patch import (
    mark_realtime_read_impl,
    read_realtime_impl,
    realtime_surface,
)
```

在构造 wakeup/surface 的地方，加载 `all_data` 后加入：

```python
surface.update(realtime_surface(all_data, include_entries=False))
```

这里的 `include_entries=False` 很重要：它只把“有多少未读、当前窗口是什么、最新 id 是多少”放进 surface，不直接把屏幕内容塞进上下文。

然后在同一个 MCP server 里注册两个工具：

```python
@mcp.tool()
def read_realtime(
    window_name="@current",
    since_id=None,
    limit=10,
    unread_only=True,
    mark_read=False,
):
    return read_realtime_impl(
        _load_all,
        _save_all,
        window_name,
        since_id,
        limit,
        unread_only,
        mark_read,
    )


@mcp.tool()
def mark_realtime_read(up_to_id=None, window_name=None):
    return mark_realtime_read_impl(_load_all, _save_all, up_to_id, window_name)
```

`_load_all` 和 `_save_all` 是占位名，要替换成小克 MCP 服务里真实的读写函数名。它们需要满足：

```python
def load_all() -> dict: ...
def save_all(data: dict) -> None: ...
```

## 4. 重启 MCP 服务

先看真实进程名，不要猜：

```bash
ssh "$GAZE_REMOTE_HOST" "pm2 status"
```

如果服务确实由 pm2 管理：

```bash
ssh "$GAZE_REMOTE_HOST" "pm2 restart <实际进程名>"
```

如果是 systemd，就用实际 unit 名：

```bash
ssh "$GAZE_REMOTE_HOST" "systemctl status <实际服务名>"
ssh "$GAZE_REMOTE_HOST" "sudo systemctl restart <实际服务名>"
```

重启后，在小克可用的 MCP 工具列表里应该能看到：

- `read_realtime`
- `mark_realtime_read`

## 5. 正式验收一条 harmless payload

前面临时 store 成功、MCP 服务也能启动后，再写一条很短的真实 `_realtime:*` 测试数据：

```bash
printf '%s\n' '{"caption":"gaze production smoke test","window":"deploy-test","source":"manual"}' \
  | ssh "$GAZE_REMOTE_HOST" "python3 $GAZE_REMOTE_DIR/push_caption.py"
```

然后让小克调用：

```text
read_realtime(window_name=None, limit=5, unread_only=False)
```

如果能读到 `gaze production smoke test`，说明链路通了。

读完后可以调用：

```text
mark_realtime_read()
```

## 6. Mac 本地端开始推送

确认 `.env` 里有：

```bash
GAZE_SSH_HOST=migratorybird
GAZE_REMOTE_COMMAND=python3 /root/mcp-memory-server/push_caption.py
GAZE_TTL_SECONDS=21600
```

先干跑：

```bash
python gaze_local.py --once --dry-run --follow-active-window --auto-mask --mask-preset mac-safe --caption-provider none
```

确认没有敏感区域后，再试一次真实上传：

```bash
python gaze_local.py --once --follow-active-window --auto-mask --mask-preset mac-safe --caption-provider none
```

长期运行时建议从启动器开：

```bash
python gaze_launcher.py
```

如果当前 Python 没有 Tkinter，它会自动打开本地网页启动器。

## 常见问题

### 需要改本地 MCP 配置吗？

通常不需要。这个工具接的是小克已经在用的 cognition MCP 服务。只有在小克那边还没有登记 cognition MCP server 时，才需要改客户端的 MCP 配置。

### 小克会自动看到所有屏幕内容吗？

不会。默认 surface 只给未读数和最新 id。小克需要主动调用 `read_realtime` 才会读内容。

### 怎么停掉？

停 Mac 端进程就不会继续上传。服务端旧条目默认 6 小时后清掉。要彻底撤掉，就从 MCP 服务里移除两个工具和 import，重启服务。

### 出问题怎么回滚？

1. 停掉 Mac 端 `gaze_local.py` 或启动器。
2. 从 MCP 服务代码里删除 `cognition_gaze_patch` import、surface 那一行、两个 `@mcp.tool()`。
3. 重启 MCP 服务。
4. 如需清理测试数据，先备份 store，再只删除 `_realtime:*` keys。
