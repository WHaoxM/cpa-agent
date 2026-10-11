"""角色配置加载器

加载和管理专家角色配置，支持从YAML文件加载角色定义。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml


class RoleConfig:
    """角色配置"""

    def __init__(self, data: Dict[str, Any]):
        self.data = data
        self.role = data.get('role', {})

    @property
    def id(self) -> str:
        return self.role.get('id', '')

    @property
    def name(self) -> str:
        return self.role.get('name', '')

    @property
    def title(self) -> str:
        return self.role.get('title', '')

    @property
    def company(self) -> str:
        return self.role.get('company', '')

    @property
    def experience(self) -> str:
        return self.role.get('experience', '')

    @property
    def expertise(self) -> List[str]:
        return self.role.get('expertise', [])

    @property
    def persona(self) -> Dict[str, Any]:
        return self.role.get('persona', {})

    @property
    def knowledge(self) -> Dict[str, Any]:
        return self.role.get('knowledge', {})

    @property
    def prompt(self) -> Dict[str, str]:
        return self.role.get('prompt', {})

    @property
    def tools(self) -> List[str]:
        return self.role.get('tools', [])

    @property
    def config(self) -> Dict[str, Any]:
        return self.role.get('config', {})

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            'id': self.id,
            'name': self.name,
            'title': self.title,
            'company': self.company,
            'experience': self.experience,
            'expertise': self.expertise,
            'persona': self.persona,
            'tools': self.tools,
        }


class RoleLoader:
    """角色加载器"""

    def __init__(self, roles_dir: str = None):
        if roles_dir is None:
            roles_dir = str(Path(__file__).parent / 'defaults' / 'roles')
        self.roles_dir = Path(roles_dir)
        self._cache: Dict[str, RoleConfig] = {}

    def load_all(self) -> Dict[str, RoleConfig]:
        """加载所有角色配置"""
        if self._cache:
            return self._cache

        if not self.roles_dir.exists():
            return {}

        for yaml_file in self.roles_dir.glob('*.yaml'):
            try:
                with open(yaml_file, 'r', encoding='utf-8') as f:
                    data = yaml.safe_load(f)
                if data and 'role' in data:
                    role_config = RoleConfig(data)
                    self._cache[role_config.id] = role_config
            except Exception as e:
                print(f"Failed to load role {yaml_file}: {e}")

        return self._cache

    def get_role(self, role_id: str) -> Optional[RoleConfig]:
        """获取指定角色配置"""
        if not self._cache:
            self.load_all()
        return self._cache.get(role_id)

    def list_roles(self) -> List[Dict[str, str]]:
        """列出所有可用角色"""
        if not self._cache:
            self.load_all()

        return [
            {
                'id': role.id,
                'name': role.name,
                'title': role.title,
                'company': role.company,
                'expertise': ', '.join(role.expertise[:3]),
            }
            for role in self._cache.values()
        ]

    def reload(self):
        """重新加载所有角色"""
        self._cache.clear()
        self.load_all()


# 全局实例
_role_loader: Optional[RoleLoader] = None


def get_role_loader() -> RoleLoader:
    """获取角色加载器单例"""
    global _role_loader
    if _role_loader is None:
        _role_loader = RoleLoader()
    return _role_loader


def get_role(role_id: str) -> Optional[RoleConfig]:
    """获取指定角色"""
    return get_role_loader().get_role(role_id)


def list_roles() -> List[Dict[str, str]]:
    """列出所有角色"""
    return get_role_loader().list_roles()


def reload_roles():
    """重新加载所有角色"""
    get_role_loader().reload()
