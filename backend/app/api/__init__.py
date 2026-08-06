"""
API 路由模块
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
profile_bp = Blueprint('profile', __name__)
match_bp = Blueprint('match', __name__)
report_bp = Blueprint('report', __name__)
agent_bp = Blueprint('agent', __name__)
learning_bp = Blueprint('learning', __name__)
admin_bp = Blueprint('admin', __name__)
favorites_bp = Blueprint('favorites', __name__)
career_bp = Blueprint('career', __name__)
messages_bp = Blueprint('messages', __name__)
ability_bp = Blueprint('ability', __name__)
ai_bp = Blueprint('ai', __name__)

from . import graph      # noqa: E402, F401
from . import profile    # noqa: E402, F401
from . import match      # noqa: E402, F401
from . import report     # noqa: E402, F401
from . import agent      # noqa: E402, F401
from . import agent_media  # noqa: E402, F401 — files / resources / voice turns
from . import pipeline   # noqa: E402, F401 - pipeline.py defines its own pipeline_bp
from . import learning   # noqa: E402, F401
from . import admin      # noqa: E402, F401
from . import favorites  # noqa: E402, F401
from . import career    # noqa: E402, F401
from . import messages  # noqa: E402, F401
from . import ability   # noqa: E402, F401
from . import ai_analysis  # noqa: E402, F401
from .pipeline_ext_common import pipeline_ext_bp  # noqa: E402, F401 - P1 阶段12: 原 pipeline_ext.py 已删除, bp 从 common 导入
from . import pipeline_ext_normalizer  # noqa: E402, F401 - P1 阶段2 拆出
from . import pipeline_ext_assessment  # noqa: E402, F401 - P1 阶段3 拆出
from . import pipeline_ext_parse_text  # noqa: E402, F401 - P1 阶段4 拆出
from . import pipeline_ext_portrait  # noqa: E402, F401 - P1 阶段5 拆出 (L2)
from . import pipeline_ext_report  # noqa: E402, F401 - P1 阶段5 拆出 (L4)
from . import pipeline_ext_vector  # noqa: E402, F401 - P1 阶段6 拆出
from . import pipeline_ext_upload  # noqa: E402, F401 - P1 阶段7 拆出
from . import pipeline_ext_graph  # noqa: E402, F401 - P1 阶段8 拆出
from . import pipeline_ext_match  # noqa: E402, F401 - P1 阶段9 拆出
from . import pipeline_ext_extract  # noqa: E402, F401 - P1 阶段10 拆出
from . import pipeline_ext_agent  # noqa: E402, F401 - P1 阶段11 拆出 (Agent + /db/health)
from . import admin_kb   # noqa: E402, F401

from .pipeline import pipeline_bp
