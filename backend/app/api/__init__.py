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

from . import graph      # noqa: E402, F401
from . import profile    # noqa: E402, F401
from . import match      # noqa: E402, F401
from . import report     # noqa: E402, F401
from . import agent      # noqa: E402, F401
from . import pipeline   # noqa: E402, F401 - pipeline.py defines its own pipeline_bp
from . import learning   # noqa: E402, F401
from . import admin      # noqa: E402, F401
from . import favorites  # noqa: E402, F401
from . import pipeline_ext  # noqa: E402, F401

from .pipeline import pipeline_bp
