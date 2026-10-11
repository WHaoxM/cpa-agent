"""
本地开发认证中间件（Dev Auth）

仅在显式 CPA_DEV_AUTH=1、CPA_DEMO_MODE=false、请求来自 loopback 且无真实认证适配器时激活：
- 只读取 X-Dev-Student-Id 请求头；缺失时不注入身份
- 构造 Principal 写入 flask.g，供 resolve_request_scope 消费
- 生产部署应由真实认证中间件提供 g.cpa_principal，此中间件不生效

安全边界：
- 仅在未检测到已有 Principal 时注入（不覆盖真实认证）
- 默认关闭；不能靠请求参数或默认 student_id 在 strict 模式伪造已认证身份
"""

from __future__ import annotations

import os

from flask import Flask, g, request

from ..utils.logger import get_logger

logger = get_logger("cpa-agent.middleware.dev_auth")

_TRUTHY = {"1", "true", "yes", "on"}
_LOOPBACK = {"127.0.0.1", "::1"}


def _dev_auth_enabled() -> bool:
    return os.environ.get("CPA_DEV_AUTH", "").strip().lower() in _TRUTHY


class DevAuthMiddleware:
    """本地开发认证中间件"""

    def __init__(self, app: Flask | None = None):
        self.app = app
        if app:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        if not _dev_auth_enabled():
            logger.info("DevAuthMiddleware 已禁用 (CPA_DEV_AUTH=0)")
            return
        app.before_request(self._inject_dev_principal)
        logger.info("DevAuthMiddleware 已注册 — 仅 loopback 的 X-Dev-Student-Id 头可注入身份")

    @staticmethod
    def _inject_dev_principal() -> None:
        from ..api._helpers import Principal
        from ..config import Config

        # demo 模式或已有认证适配器设置的 principal → 跳过
        if getattr(Config, "DEMO_MODE", True):
            return
        if _has_existing_principal():
            return
        if request.remote_addr not in _LOOPBACK:
            return

        student_id = request.headers.get("X-Dev-Student-Id", "").strip()
        if not student_id:
            return
        principal_id = f"dev:{student_id}"
        g.cpa_principal = Principal(
            principal_id=principal_id,
            student_id=student_id,
            source="dev_middleware",
        )


def _has_existing_principal() -> bool:
    """检查是否已有认证适配器设置了 principal"""
    from ..api._helpers import Principal
    raw = getattr(g, "cpa_principal", None) or getattr(g, "principal", None)
    if isinstance(raw, Principal):
        return True
    if isinstance(raw, dict):
        sid = raw.get("student_id") or raw.get("studentId") or raw.get("user_id")
        pid = raw.get("principal_id") or raw.get("sub") or raw.get("id") or sid
        if sid and pid:
            return True
    if getattr(g, "student_id", None) or getattr(g, "user_id", None):
        return True
    return False
