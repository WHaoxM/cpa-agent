"""
Pipeline_ext 公共模块

存放 pipeline_ext 拆分后跨子模块共享的对象:
- pipeline_ext_bp: Flask 蓝图单例 (所有子模块都从此 import)
- logger: 日志记录器
- _normalize_skills: 技能列表归一化 helper (被 extract/match 子模块共享)

设计原则: 只放真正跨模块共享的对象, 不放业务路由。
"""

from flask import Blueprint

from ..utils.logger import get_logger

logger = get_logger('cpa-agent.api.pipeline_ext')

# 蓝图单例: 所有 pipeline_ext_* 子模块都 from .pipeline_ext_common import pipeline_ext_bp
pipeline_ext_bp = Blueprint('pipeline_ext', __name__, url_prefix='/api/pipeline/ext')


def _normalize_skills(skills):
    """将字符串型技能列表归一化为 dict 列表 (portrait_generator 期望 [{name, ...}])"""
    normalized = []
    for s in skills or []:
        if isinstance(s, str):
            normalized.append({"name": s, "category": "其他", "confidence": 0.7})
        elif isinstance(s, dict):
            # 确保 name 字段存在
            if "name" not in s and "skill" in s:
                s = {**s, "name": s["skill"]}
            normalized.append(s)
    return normalized
