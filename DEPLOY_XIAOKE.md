# 给小克接入独立 Gaze MCP 的部署工单

## 先讲人话

gaze 现在不是 memory MCP 的一部分。它是一套独立链路：

```text
Isa 的 Mac
  gaze_local.py 截屏/OCR/描述
      |
      | ssh
      v
我们的 VPS
  gaze_push_caption.py 接收 JSON
      |
      v
  /home/linuxuser/search_tool/gaze_realtime.json
      |
      v
独立 gaze MCP
  https://migratorybird.xyz/mcp/gaze/
      |
      v
小克按需调用 read_realtime
```

memory MCP 只管记忆/日记/图谱；gaze MCP 只管临时读屏缓存。

## 当前部署状态

```text
SSH: linuxuser@45.76.219.241
服务目录: /home/linuxuser/search_tool
实时 store: /home/linuxuser/search_tool/gaze_realtime.json
gaze MCP 脚本: /home/linuxuser/search_tool/gaze_mcp_server.py
gaze MCP 本机端口: 127.0.0.1:8772
gaze MCP 公网 URL: https://migratorybird.xyz/mcp/gaze/
进程管理: pm2 gaze-mcp
token env: /home/linuxuser/search_tool/.env 里的 GAZE_MCP_TOKEN
本地私密连接文件: .gaze_mcp_connection.txt
```

## 安全边界

- gaze 不写 `/home/linuxuser/.mcp/memory.jsonl`。
- gaze 正式实时数据只写 `/home/linuxuser/search_tool/gaze_realtime.json`。
- gaze MCP 使用独立 `GAZE_MCP_TOKEN`，不复用 memory MCP token。
- `.gaze_mcp_connection.txt` 和 `.env` 都不提交到 GitHub。
- 服务端旧条目默认 6 小时清理，保持临时缓存属性。
- memory MCP 工具列表里不应该出现 `read_realtime` 或 `mark_realtime_read`。

## 0. 本地准备

```bash
cd "/Users/Isa/Projects/gaze-xiaoke-tool"
. .venv/bin/activate
./safe_check.sh
```

## 1. 上传服务端文件

```bash
export GAZE_REMOTE_HOST=linuxuser@45.76.219.241
export GAZE_REMOTE_DIR=/home/linuxuser/search_tool

scp push_caption.py "$GAZE_REMOTE_HOST:$GAZE_REMOTE_DIR/gaze_push_caption.py"
scp cognition_gaze_patch.py "$GAZE_REMOTE_HOST:$GAZE_REMOTE_DIR/gaze_realtime_tools.py"
scp gaze_mcp_server.py "$GAZE_REMOTE_HOST:$GAZE_REMOTE_DIR/gaze_mcp_server.py"
ssh "$GAZE_REMOTE_HOST" "chmod +x $GAZE_REMOTE_DIR/gaze_push_caption.py"
```

## 2. 配置独立 token 和 store

不要把 token 打印到聊天或提交到仓库。服务器 `.env` 至少需要：

```bash
GAZE_REALTIME_PATH=/home/linuxuser/search_tool/gaze_realtime.json
GAZE_MCP_PORT=8772
GAZE_MCP_TOKEN=<server-side-secret>
```

## 3. 启动独立 gaze MCP

```bash
ssh "$GAZE_REMOTE_HOST" '
cd /home/linuxuser/search_tool
python3 -m py_compile gaze_mcp_server.py gaze_realtime_tools.py gaze_push_caption.py
pm2 delete gaze-mcp >/dev/null 2>&1 || true
pm2 start /home/linuxuser/search_tool/gaze_mcp_server.py --interpreter python3 --name gaze-mcp
pm2 save
pm2 status gaze-mcp
'
```

本机健康检查：

```bash
ssh "$GAZE_REMOTE_HOST" "curl -sS http://127.0.0.1:8772/health"
```

## 4. 配置 nginx 路径

公网路径：

```text
https://migratorybird.xyz/mcp/gaze/
```

nginx location：

```nginx
location /mcp/gaze/ {
        proxy_pass http://127.0.0.1:8772/mcp/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header Connection "";
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 120s;
        chunked_transfer_encoding on;
}
```

每次修改 nginx 后：

```bash
sudo nginx -t && sudo systemctl reload nginx
```

备份不要放在 `/etc/nginx/sites-enabled/`，否则 nginx 会把备份也当配置加载。放到 `/etc/nginx/backups/`。

## 5. 验证公网 MCP

无 token 应该是 401：

```bash
curl -s -o /tmp/gaze-public-no-token.txt -w "%{http_code}\n" \
  https://migratorybird.xyz/mcp/gaze/
```

带 token 的 initialize 应该返回 `serverInfo.name = "xike-gaze"`。

```bash
TOKEN=$(ssh "$GAZE_REMOTE_HOST" 'python3 - <<'"'"'PY'"'"'
from dotenv import dotenv_values
print(dotenv_values("/home/linuxuser/search_tool/.env").get("GAZE_MCP_TOKEN", ""))
PY
')

curl -sS \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"smoke","version":"1"}}}' \
  "https://migratorybird.xyz/mcp/gaze/?token=$TOKEN"
```

## 6. 确认 memory MCP 已隔离

memory MCP 工具列表不应包含 realtime：

```bash
ssh "$GAZE_REMOTE_HOST" 'cd /home/linuxuser/search_tool && python3 - <<'"'"'PY'"'"'
import asyncio
import memory_server

tools = asyncio.run(memory_server.list_tools())
names = [tool.name for tool in tools]
print(any("realtime" in name for name in names))
print(names)
PY'
```

期望第一行是：

```text
False
```

## 7. Mac 本地端开始推送

`.env` 里应有：

```bash
GAZE_SSH_HOST=linuxuser@45.76.219.241
GAZE_REMOTE_COMMAND=GAZE_STORE_PATH=/home/linuxuser/search_tool/gaze_realtime.json GAZE_TTL_SECONDS=21600 python3 /home/linuxuser/search_tool/gaze_push_caption.py
GAZE_MCP_URL=https://migratorybird.xyz/mcp/gaze/
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

## 常见问题

### 小克该填哪个 URL？

填：

```text
https://migratorybird.xyz/mcp/gaze/
```

token 在 `.gaze_mcp_connection.txt` 里；如果客户端不能单独填 Bearer token，就用文件里的 `URL with token`。

### 小克会自动看到所有屏幕内容吗？

不会。gaze MCP 只提供工具。小克需要主动调用 `read_realtime` 才会读内容。

### 怎么停掉？

```bash
ssh linuxuser@45.76.219.241 'pm2 stop gaze-mcp'
```

Mac 端停止 `gaze_local.py` 或启动器后，就不会继续上传。

### 出问题怎么回滚？

1. 停 Mac 端 `gaze_local.py` 或启动器。
2. `pm2 stop gaze-mcp`。
3. 从 nginx 移除 `/mcp/gaze/` location。
4. `sudo nginx -t && sudo systemctl reload nginx`。
5. 保持 memory MCP 不动；它已经不依赖 gaze。
