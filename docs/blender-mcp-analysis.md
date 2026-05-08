# blender-mcp 架构分析与接入方案

## 整体架构

blender-mcp 由两个独立组件构成：

```
┌──────────────┐     MCP Protocol (stdio)     ┌──────────────┐
│  AI Client   │ ◄──────────────────────────► │  server.py   │
│ (opencode /  │                              │  (FastMCP)   │
│  Claude)     │                              │              │
└──────────────┘                              └──────┬───────┘
                                                     │
                                            TCP Socket (localhost:9876)
                                                     │
                                              ┌──────┴───────┐
                                              │  addon.py    │
                                              │ (Blender插件) │
                                              └──────────────┘
```

### addon.py（Blender 端）

- 纯 TCP socket server，监听 `localhost:9876`
- 接收 JSON 格式命令，在 Blender 主线程执行，返回 JSON 响应
- 核心命令类型：
  | type | 功能 |
  |------|------|
  | `execute_code` | 执行任意 Blender Python 代码 |
  | `get_scene_info` | 获取场景基本信息 |
  | `get_object_info` | 获取对象详情 |
  | `get_viewport_screenshot` | 截取3D视口 |
  | `get_polyhaven_status` 等 | 素材库状态查询 |

### server.py（中间层，可选）

- 使用 `mcp[cli]` 包的 FastMCP 框架
- 通过 MCP 协议暴露工具给 AI Client
- 内部维护 `BlenderConnection` 类，封装了与 addon.py 的 TCP 通信

## 关键发现：server.py 是多余的

**addon.py 的 `execute_code` 命令本质上是一个通用通道**——任意 `bpy.ops.*` 调用都可以通过它执行：

```json
{"type": "execute_code", "params": {"code": "bpy.ops.modder.direct_convert()"}}
```

这意味着 **完全不需要 MCP 协议层（server.py）**。任何能发 TCP socket 的程序都能直接控制 Blender。

## 推荐方案：FastAPI 直连 TCP

```
浏览器 (localhost)
  ↓
FastAPI 后端
  ↓ TCP Socket (localhost:9876)
Blender addon
  └── bpy.ops.modder.* / bpy.ops.mhws.* 等
```

### 核心代码（可直接嵌入 FastAPI）

```python
import socket
import json

class BlenderConnection:
    def __init__(self, host='localhost', port=9876):
        self.host = host
        self.port = port
        self.sock = None

    def connect(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.connect((self.host, self.port))

    def send_command(self, command_type: str, params: dict = None) -> dict:
        command = {"type": command_type, "params": params or {}}
        self.sock.sendall(json.dumps(command).encode('utf-8'))
        data = self.sock.recv(8192)
        response = json.loads(data.decode('utf-8'))
        if response.get("status") == "error":
            raise Exception(response.get("message"))
        return response.get("result", {})

    def execute_code(self, code: str) -> dict:
        return self.send_command("execute_code", {"code": code})

    def close(self):
        if self.sock:
            self.sock.close()
```

### 调用 Modding Toolkit Operator 示例

```python
blender = BlenderConnection()
blender.connect()

# 对齐骨骼
blender.execute_code("bpy.ops.modder.universal_snap()")

# 重命名顶点组
blender.execute_code("bpy.ops.modder.direct_convert()")

# 方向计算
blender.execute_code("bpy.ops.modder.tpose_direction()")

blender.close()
```

## 前置条件处理

部分 Operator 需要先设置 scene 属性（如选择 X/Y 预设），在执行前通过 `execute_code` 设置：

```python
settings = "bpy.context.scene.mhw_suite_settings"
blender.execute_code(f"{settings}.import_preset_enum = 'VRChat标准.json'")
blender.execute_code(f"{settings}.target_preset_enum = '怪猎荒野.json'")
blender.execute_code("bpy.ops.modder.universal_snap()")
```

## 与其他接入方案对比

| 方案 | 依赖 | 复杂度 | 适用场景 |
|------|------|--------|----------|
| **TCP直连** | Python标准库`socket` | 低 | FastAPI 后端 → Blender |
| MCP + opencode | `mcp[cli]`, opencode | 中 | 已有 opencode 的AI Agent |
| MCP + Claude Desktop | `mcp[cli]`, Claude Desktop | 中 | 本地 Claude 使用 |

## 结论

对于 REE-ModPilot 项目：
- **FastAPI 后端直接连 `localhost:9876` TCP socket** 即可完全控制 Blender
- 不需要 opencode 平台
- 不需要 MCP 协议
- 不需要 server.py
- 仅需将 `plugin_api.md` 中的 Operator 封装为 FastAPI endpoint 或 Agent tool function
- 前置条件（选中对象、设置预设、进入模式等）通过前置 `execute_code` 调用来满足
