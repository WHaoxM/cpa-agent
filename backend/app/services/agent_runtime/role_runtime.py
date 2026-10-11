"""角色运行时 - 管理专家角色对话

提供与专家角色对话的能力，支持：
1. 角色切换
2. 角色上下文注入
3. 角色专属工具
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from .mode_runtime import ModeRuntime
from config.role_loader import get_role, list_roles, RoleConfig


class RoleRuntime:
    """角色运行时"""

    def __init__(self, mode_runtime: ModeRuntime):
        self.mode_runtime = mode_runtime

    async def chat_with_role(
        self,
        role_id: str,
        user_message: str,
        session_id: str,
        student_id: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """
        与专家角色对话

        Args:
            role_id: 角色ID
            user_message: 用户消息
            session_id: 会话ID
            student_id: 学生ID

        Returns:
            对话结果
        """
        # 1. 加载角色配置
        role_config = get_role(role_id)
        if not role_config:
            return {
                'error': f'角色 {role_id} 不存在',
                'available_roles': [r['id'] for r in list_roles()],
            }

        # 2. 组装角色上下文
        role_context = self._build_role_context(role_config)

        # 3. 注入角色prompt
        system_prompt = role_config.prompt.get('system', '')

        # 4. 调用ModeRuntime（使用Agent模式）
        result = await self.mode_runtime.chat(
            message=user_message,
            session_id=session_id,
            student_id=student_id,
            interaction_mode='agent',
            system_override=system_prompt,
            role_context=role_context,
            **kwargs,
        )

        # 5. 添加角色元数据
        result['role'] = role_config.to_dict()

        return result

    def _build_role_context(self, role_config: RoleConfig) -> str:
        """构建角色上下文"""
        parts = []

        # 角色基本信息
        parts.append(f"当前对话角色：{role_config.name}，{role_config.title}，{role_config.company}")

        # 经验
        if role_config.experience:
            parts.append(f"经验背景：{role_config.experience}")

        # 专业领域
        if role_config.expertise:
            parts.append(f"专业领域：{', '.join(role_config.expertise)}")

        # 人设特点
        persona = role_config.persona
        if persona.get('traits'):
            parts.append(f"性格特点：{', '.join(persona['traits'])}")
        if persona.get('communication_style'):
            parts.append(f"沟通风格：{persona['communication_style']}")

        # 核心理念
        if persona.get('values'):
            parts.append(f"核心理念：{persona['values']}")

        # 知识领域
        knowledge = role_config.knowledge
        if knowledge:
            knowledge_topics = list(knowledge.keys())
            if knowledge_topics:
                parts.append(f"知识领域：{', '.join(knowledge_topics)}")

        return '\n'.join(parts)

    def get_role_tools(self, role_id: str) -> List[str]:
        """获取角色可用的工具"""
        role_config = get_role(role_id)
        if not role_config:
            return []
        return role_config.tools

    def get_role_greeting(self, role_id: str) -> str:
        """获取角色问候语"""
        role_config = get_role(role_id)
        if not role_config:
            return ""
        return role_config.prompt.get('greeting', '')

    def get_role_knowledge(self, role_id: str, topic: str) -> Optional[str]:
        """获取角色知识"""
        role_config = get_role(role_id)
        if not role_config:
            return None

        knowledge = role_config.knowledge.get(topic)
        if knowledge:
            if isinstance(knowledge, list):
                return '\n'.join(f"- {item}" for item in knowledge)
            return str(knowledge)
        return None


def get_role_runtime(mode_runtime: ModeRuntime = None) -> RoleRuntime:
    """获取角色运行时"""
    if mode_runtime is None:
        mode_runtime = ModeRuntime()
    return RoleRuntime(mode_runtime)
