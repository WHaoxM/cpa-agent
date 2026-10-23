"""MCP适配器

借鉴OpenCode的MCP系统，为CPA-Agent提供：
1. MCP客户端基础
2. 工具转换（MCP工具 → 内部Tool格式）
3. 多传输协议支持（Stdio/HTTP）
"""

from __future__ import annotations

import json
import subprocess
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass, field
from enum import Enum
import threading


class MCPTransportType(Enum):
    """MCP传输类型"""
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class MCPStatus(Enum):
    """MCP状态"""
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    FAILED = "failed"
    NEEDS_AUTH = "needs_auth"


@dataclass
class MCPToolDef:
    """MCP工具定义"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Optional[Dict[str, Any]] = None


@dataclass
class MCPServerConfig:
    """MCP服务器配置"""
    name: str
    transport: MCPTransportType
    command: Optional[str] = None  # Stdio传输
    args: Optional[List[str]] = None  # Stdio传输
    url: Optional[str] = None  # HTTP/SSE传输
    env: Optional[Dict[str, str]] = None
    timeout: int = 30000  # 毫秒


@dataclass
class MCPServerState:
    """MCP服务器状态"""
    config: MCPServerConfig
    status: MCPStatus = MCPStatus.DISCONNECTED
    tools: List[MCPToolDef] = field(default_factory=list)
    error: Optional[str] = None
    process: Optional[subprocess.Popen] = None


class MCPClient:
    """MCP客户端"""
    
    def __init__(self, config: MCPServerConfig):
        self.config = config
        self.state = MCPServerState(config=config)
        self._lock = threading.Lock()
    
    def connect(self) -> bool:
        """连接到MCP服务器"""
        with self._lock:
            try:
                if self.config.transport == MCPTransportType.STDIO:
                    return self._connect_stdio()
                elif self.config.transport in (MCPTransportType.HTTP, MCPTransportType.SSE):
                    return self._connect_http()
                else:
                    self.state.status = MCPStatus.FAILED
                    self.state.error = f"Unsupported transport: {self.config.transport}"
                    return False
            except Exception as e:
                self.state.status = MCPStatus.FAILED
                self.state.error = str(e)
                return False
    
    def _connect_stdio(self) -> bool:
        """连接Stdio传输"""
        if not self.config.command:
            self.state.error = "No command specified for Stdio transport"
            return False
        
        env = {**__import__('os').environ}
        if self.config.env:
            env.update(self.config.env)
        
        self.state.process = subprocess.Popen(
            [self.config.command] + (self.config.args or []),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        
        # 发送初始化请求
        init_request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {
                    "name": "cpa-agent",
                    "version": "1.0.0"
                }
            }
        }
        
        self._send_message(init_request)
        response = self._receive_message()
        
        if response and 'result' in response:
            self.state.status = MCPStatus.CONNECTED
            self._list_tools()
            return True
        else:
            self.state.error = "Failed to initialize"
            return False
    
    def _connect_http(self) -> bool:
        """连接HTTP/SSE传输"""
        # 简化实现，实际需要使用HTTP客户端
        self.state.error = "HTTP/SSE transport not implemented yet"
        return False
    
    def _send_message(self, message: Dict[str, Any]):
        """发送消息"""
        if not self.state.process:
            return
        
        data = json.dumps(message)
        self.state.process.stdin.write(f"{data}\n".encode())
        self.state.process.stdin.flush()
    
    def _receive_message(self) -> Optional[Dict[str, Any]]:
        """接收消息"""
        if not self.state.process:
            return None
        
        line = self.state.process.stdout.readline()
        if line:
            return json.loads(line.decode())
        return None
    
    def _list_tools(self):
        """获取工具列表"""
        list_request = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {}
        }
        
        self._send_message(list_request)
        response = self._receive_message()
        
        if response and 'result' in response:
            tools = response['result'].get('tools', [])
            self.state.tools = [
                MCPToolDef(
                    name=tool['name'],
                    description=tool.get('description', ''),
                    input_schema=tool.get('inputSchema', {}),
                )
                for tool in tools
            ]
    
    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """调用工具"""
        with self._lock:
            if self.state.status != MCPStatus.CONNECTED:
                raise ValueError(f"MCP server not connected: {self.state.error}")
            
            call_request = {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": arguments
                }
            }
            
            self._send_message(call_request)
            response = self._receive_message()
            
            if response and 'result' in response:
                content = response['result'].get('content', [])
                if content and len(content) > 0:
                    return content[0].get('text', '')
            elif response and 'error' in response:
                raise ValueError(f"MCP error: {response['error']}")
            
            return None
    
    def disconnect(self):
        """断开连接"""
        with self._lock:
            if self.state.process:
                self.state.process.terminate()
                self.state.process = None
            self.state.status = MCPStatus.DISCONNECTED
            self.state.tools = []
    
    def get_tools(self) -> List[MCPToolDef]:
        """获取可用工具列表"""
        return self.state.tools
    
    def get_status(self) -> MCPStatus:
        """获取状态"""
        return self.state.status


class MCPManager:
    """MCP管理器"""
    
    def __init__(self):
        self._clients: Dict[str, MCPClient] = {}
        self._configs: Dict[str, MCPServerConfig] = {}
    
    def add_server(self, config: MCPServerConfig):
        """添加MCP服务器"""
        self._configs[config.name] = config
    
    def connect_server(self, name: str) -> bool:
        """连接到MCP服务器"""
        config = self._configs.get(name)
        if not config:
            return False
        
        client = MCPClient(config)
        if client.connect():
            self._clients[name] = client
            return True
        return False
    
    def disconnect_server(self, name: str):
        """断开MCP服务器"""
        client = self._clients.get(name)
        if client:
            client.disconnect()
            del self._clients[name]
    
    def get_client(self, name: str) -> Optional[MCPClient]:
        """获取MCP客户端"""
        return self._clients.get(name)
    
    def list_servers(self) -> List[Dict[str, Any]]:
        """列出所有服务器"""
        result = []
        for name, config in self._configs.items():
            client = self._clients.get(name)
            result.append({
                'name': name,
                'transport': config.transport.value,
                'status': client.get_status().value if client else 'disconnected',
                'tools_count': len(client.get_tools()) if client else 0,
            })
        return result
    
    def get_all_tools(self) -> Dict[str, MCPToolDef]:
        """获取所有可用工具"""
        tools = {}
        for name, client in self._clients.items():
            if client.get_status() == MCPStatus.CONNECTED:
                for tool in client.get_tools():
                    tool_name = f"{name}_{tool.name}"
                    tools[tool_name] = tool
        return tools
    
    def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """调用工具"""
        client = self._clients.get(server_name)
        if not client:
            raise ValueError(f"MCP server not found: {server_name}")
        
        return client.call_tool(tool_name, arguments)
    
    def connect_all(self):
        """连接所有配置的服务器"""
        for name in self._configs:
            self.connect_server(name)
    
    def disconnect_all(self):
        """断开所有服务器"""
        for name in list(self._clients.keys()):
            self.disconnect_server(name)


# 全局实例
_mcp_manager: Optional[MCPManager] = None


def get_mcp_manager() -> MCPManager:
    """获取MCP管理器单例"""
    global _mcp_manager
    if _mcp_manager is None:
        _mcp_manager = MCPManager()
    return _mcp_manager


def initialize_mcp(configs: List[MCPServerConfig]):
    """初始化MCP系统"""
    manager = get_mcp_manager()
    for config in configs:
        manager.add_server(config)
    manager.connect_all()
