"""
Session blackboard with whitelist keys and single-writer ACL.
Business artifacts live as id/summary pointers, not full JSON dumps.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional, Set

from ...utils.logger import get_logger

logger = get_logger("cpa-agent.agent_runtime.blackboard")

# Canonical whitelist
BLACKBOARD_KEYS = frozenset({
    "student.profile_ref",
    "portrait.current",
    "match.current",
    "report.current",
    "plan.current",
    "constraints.user",
    "orchestration.meta",
    "attachments.mounted",
})

# Single-writer ACL (Supervisor may also write student.profile_ref / constraints / orchestration)
WRITERS: Dict[str, frozenset] = {
    "student.profile_ref": frozenset({"supervisor", "profile"}),
    "portrait.current": frozenset({"profile"}),
    "match.current": frozenset({"match"}),
    "report.current": frozenset({"report"}),
    "plan.current": frozenset({"planner"}),
    "constraints.user": frozenset({"supervisor"}),
    "orchestration.meta": frozenset({"supervisor"}),
    "attachments.mounted": frozenset({"supervisor"}),
}

# Default read sets per subagent
READERS: Dict[str, frozenset] = {
    "profile": frozenset({"student.profile_ref", "constraints.user", "attachments.mounted"}),
    "match": frozenset({
        "student.profile_ref", "portrait.current", "constraints.user", "attachments.mounted",
    }),
    "report": frozenset({
        "student.profile_ref", "portrait.current", "match.current",
        "constraints.user", "attachments.mounted",
    }),
    "planner": frozenset({
        "student.profile_ref", "portrait.current", "match.current",
        "report.current", "constraints.user", "plan.current", "attachments.mounted",
    }),
    "qa": frozenset({
        "student.profile_ref", "portrait.current", "match.current",
        "report.current", "plan.current", "constraints.user",
        "attachments.mounted",
    }),
    "supervisor": BLACKBOARD_KEYS,
}

# Plan / match gates
GATE_REQUIRES: Dict[str, str] = {
    "match": "portrait.current",
    "report": "match.current",
}


class BlackboardError(ValueError):
    pass


class Blackboard:
    """In-memory view backed by a plain dict (usually session.context['blackboard'])."""

    def __init__(self, data: Optional[Dict[str, Any]] = None):
        self._data: Dict[str, Any] = {}
        if data:
            for k, v in data.items():
                if k in BLACKBOARD_KEYS:
                    self._data[k] = v

    def to_dict(self) -> Dict[str, Any]:
        return deepcopy(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def has(self, key: str) -> bool:
        val = self._data.get(key)
        return val is not None and val != {} and val != ""

    def read_slice(self, keys: Iterable[str]) -> Dict[str, Any]:
        out = {}
        for key in keys:
            if key not in BLACKBOARD_KEYS:
                raise BlackboardError(f"read key not in whitelist: {key}")
            if key in self._data:
                out[key] = deepcopy(self._data[key])
        return out

    def read_slice_for_model(self, keys: Iterable[str]) -> Dict[str, Any]:
        """
        Least-privilege blackboard view for LLM injection (Claude/Qoder).
        Pointers + short summaries only — never full constraint text / resume blobs / raw student_id.
        """
        raw = self.read_slice(keys)
        out: Dict[str, Any] = {}
        for key, val in raw.items():
            if not isinstance(val, dict):
                text = str(val)
                out[key] = text[:120] + ("…" if len(text) > 120 else "")
                continue
            if key == "student.profile_ref":
                out[key] = {"linked": True, "summary": "已关联当前用户"}
                continue
            if key == "constraints.user":
                out[key] = {"summary": str(val.get("summary") or "")[:200]}
                continue
            if key == "attachments.mounted":
                files = val.get("files") if isinstance(val.get("files"), list) else []
                out[key] = {
                    "summary": str(val.get("summary") or "")[:80],
                    "files": [
                        {
                            "file_id": f.get("file_id"),
                            "filename": f.get("filename"),
                            "excerpt": str(f.get("excerpt") or "")[:80],
                        }
                        for f in files[:6]
                        if isinstance(f, dict)
                    ],
                }
                continue
            proj: Dict[str, Any] = {}
            summary = val.get("summary")
            if summary is not None:
                proj["summary"] = str(summary)[:200]
            for idk in ("id", "portrait_id", "match_id", "report_id", "task_id"):
                if val.get(idk) is not None:
                    proj[idk] = val[idk]
            out[key] = proj or {"present": True}
        return out

    def write(self, key: str, value: Any, writer: str) -> None:
        if key not in BLACKBOARD_KEYS:
            raise BlackboardError(f"write key not in whitelist: {key}")
        allowed = WRITERS.get(key, frozenset())
        if writer not in allowed:
            raise BlackboardError(f"writer '{writer}' cannot write '{key}' (allowed={sorted(allowed)})")
        self._data[key] = value

    def write_many(self, updates: Dict[str, Any], writer: str) -> None:
        for key, value in updates.items():
            self.write(key, value, writer)

    def capability_card(
        self,
        max_chars: int = 800,
        *,
        user_facing: bool = False,
        filled_only: bool = False,
    ) -> str:
        """
        Compact card for Supervisor injection.
        user_facing=True → Chinese labels (safe if model echoes into THINKING/UI).
        filled_only=True → omit empty keys (Agent router must not drown in empties).
        """
        labels = {
            "student.profile_ref": "学生档案",
            "portrait.current": "个人职业画像",
            "match.current": "人岗匹配",
            "report.current": "职业报告",
            "plan.current": "学习规划",
            "constraints.user": "用户约束",
            "orchestration.meta": "编排进度",
            "attachments.mounted": "已挂载附件",
        }
        lines: List[str] = []
        for key in sorted(BLACKBOARD_KEYS):
            label = labels.get(key, key) if user_facing else key
            if not self.has(key):
                if filled_only:
                    continue
                lines.append(f"- {label}: （尚未生成）" if user_facing else f"- {key}: (empty)")
                continue
            val = self._data[key]
            if isinstance(val, dict):
                summary = val.get("summary") or val.get("id") or val.get("portrait_id") or val.get("match_id")
                if summary is None:
                    summary = ", ".join(f"{k}" for k in list(val.keys())[:6])
                # Never echo raw student_id into prompts
                if isinstance(summary, str) and summary.startswith("student_"):
                    summary = "已关联当前用户"
                lines.append(f"- {label}: {summary}")
            else:
                text = str(val)
                if len(text) > 120:
                    text = text[:117] + "..."
                lines.append(f"- {label}: {text}")
        if not lines:
            title = "【会话进度】" if user_facing else "【黑板能力卡】"
            return title + "\n- （暂无已生成业务态）"
        title = "【会话进度】" if user_facing else "【黑板能力卡】"
        card = title + "\n" + "\n".join(lines)
        if len(card) > max_chars:
            return card[: max_chars - 3] + "..."
        return card

    def check_gate(self, subagent: str) -> Optional[str]:
        """Return error message if prerequisite missing, else None."""
        req = GATE_REQUIRES.get(subagent)
        if req and not self.has(req):
            return f"cannot call {subagent}: missing blackboard key '{req}'"
        return None

    def snapshot(self, keys: Optional[Iterable[str]] = None) -> Dict[str, Any]:
        if keys is None:
            return self.to_dict()
        return self.read_slice(keys)

    def restore(self, snap: Dict[str, Any]) -> None:
        self._data = deepcopy(snap)


def default_read_keys(subagent: str) -> List[str]:
    return sorted(READERS.get(subagent, frozenset()))


def default_write_keys(subagent: str) -> List[str]:
    return sorted(k for k, writers in WRITERS.items() if subagent in writers)


def load_blackboard_from_session_context(context: Optional[Dict[str, Any]]) -> Blackboard:
    """Load from session.context — prefers nested 'blackboard', else migrate flat keys."""
    ctx = context if isinstance(context, dict) else {}
    nested = ctx.get("blackboard")
    if isinstance(nested, dict):
        return Blackboard(nested)
    # migrate legacy flat business blobs
    bb = Blackboard()
    legacy_map = {
        "portrait": "portrait.current",
        "match_result": "match.current",
        "report": "report.current",
        "plan": "plan.current",
        "constraints": "constraints.user",
    }
    writer_for = {
        "portrait.current": "profile",
        "match.current": "match",
        "report.current": "report",
        "plan.current": "planner",
        "constraints.user": "supervisor",
    }
    for old, new in legacy_map.items():
        if old in ctx and ctx[old] is not None:
            try:
                bb.write(new, ctx[old], writer_for[new])
            except BlackboardError:
                pass
    return bb
