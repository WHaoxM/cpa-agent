"""
API 路由模块
5 个 Blueprint 对应 CPA-Agent 的核心功能域
"""

from flask import Blueprint

graph_bp = Blueprint('graph', __name__)
profile_bp = Blueprint('profile', __name__)
match_bp = Blueprint('match', __name__)
report_bp = Blueprint('report', __name__)
agent_bp = Blueprint('agent', __name__)

from . import graph      # noqa: E402, F401
from . import profile    # noqa: E402, F401
from . import match      # noqa: E402, F401
from . import report     # noqa: E402, F401
from . import agent      # noqa: E402, F401
