"""
收藏与目标岗位数据访问 (Repository)
集中 favorites 相关 SQL, 与 API 层解耦
"""

import json
from typing import Optional, List, Dict, Any
from sqlalchemy import text

from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.repositories.favorites')


class SavedJobPublicationError(ValueError):
    """The requested catalog publication is stale, revoked, or unverifiable."""


def _decoded_json(value):
    if isinstance(value, str):
        return json.loads(value)
    return value


class FavoritesRepository:
    """收藏与目标岗位数据访问"""

    def get_saved_job(self, job_id: str, *, user_id: str, record_id=None):
        if not user_id:
            raise PermissionError("user_id required")
        rows = get_mysql_client().execute(
            "SELECT * FROM saved_job WHERE job_id = :job_id AND user_id = :user_id "
            + ("AND id = :record_id " if record_id is not None else "") + "ORDER BY id DESC LIMIT 1",
            {"job_id": job_id, "user_id": user_id, "record_id": record_id})
        return rows[0] if rows else None

    def get_target_role(self, role_name: str, *, user_id: str, record_id=None):
        if not user_id:
            raise PermissionError("user_id required")
        rows = get_mysql_client().execute(
            "SELECT * FROM target_role WHERE role_name = :role AND user_id = :user_id "
            + ("AND id = :record_id " if record_id is not None else "") + "ORDER BY id DESC LIMIT 1",
            {"role": role_name, "user_id": user_id, "record_id": record_id})
        return rows[0] if rows else None

    # ── 收藏岗位 ──

    def list_saved_jobs(self, user_id: str) -> List[Dict[str, Any]]:
        """查询当前用户收藏岗位，并投影其不可变发布版本状态。"""
        if not user_id:
            raise PermissionError("user_id required")
        mysql = get_mysql_client()
        sql = self._saved_job_select() + " WHERE sj.user_id = :user_id ORDER BY sj.saved_at DESC, sj.id DESC"
        return [self._project_saved_job(row) for row in mysql.fetch_all(sql, {"user_id": user_id})]

    @staticmethod
    def _saved_job_select() -> str:
        return """
        SELECT sj.*, p.id AS bound_publication_id, p.job_portrait_id AS bound_job_portrait_id,
               p.publication_version AS bound_publication_version,
               p.snapshot_json AS bound_snapshot_json, p.content_sha256 AS bound_content_sha256,
               p.status AS bound_publication_status, p.published_at AS bound_published_at,
               latest.id AS latest_publication_id
        FROM saved_job sj
        LEFT JOIN job_catalog_publication p ON p.id = sj.publication_id
        LEFT JOIN (
            SELECT job_portrait_id, MAX(id) AS latest_id
            FROM job_catalog_publication GROUP BY job_portrait_id
        ) latest_row ON latest_row.job_portrait_id = p.job_portrait_id
        LEFT JOIN job_catalog_publication latest ON latest.id = latest_row.latest_id
        """

    @staticmethod
    def _project_saved_job(row: Dict[str, Any]) -> Dict[str, Any]:
        """Do not let a damaged publication snapshot masquerade as valid data."""
        from ..services.job_publication import content_sha256, validate_snapshot

        result = dict(row)
        result.pop("user_id", None)
        bound_saved_publication_id = result.pop("publication_id", None)
        publication_id = result.pop("bound_publication_id", None)
        portrait_id = result.pop("bound_job_portrait_id", None)
        version = result.pop("bound_publication_version", None)
        raw_snapshot = result.pop("bound_snapshot_json", None)
        digest = result.pop("bound_content_sha256", None)
        publication_status = result.pop("bound_publication_status", None)
        published_at = result.pop("bound_published_at", None)
        latest_id = result.pop("latest_publication_id", None)
        result["saved_job_id"] = result["id"]

        if bound_saved_publication_id is None:
            result.update({
                "publication_status": "legacy_unverified",
                "source_verified": False,
                "vacancy_validity": "unknown",
                "publication": None,
                "publication_snapshot": None,
            })
            return result

        result["job_portrait_id"] = portrait_id or result.get("job_id")
        result["publication_version"] = version
        result["source_verified"] = False
        result["vacancy_validity"] = "unknown"
        try:
            snapshot = _decoded_json(raw_snapshot)
            validate_snapshot(snapshot)
            valid = (publication_id is not None and portrait_id == result.get("job_id")
                     and isinstance(digest, str) and digest == content_sha256(snapshot)
                     and snapshot.get("review_state") == "approved")
        except (TypeError, ValueError, json.JSONDecodeError):
            snapshot, valid = None, False

        if not valid:
            status = "unavailable"
            snapshot = None
        elif publication_status == "revoked":
            status = "revoked"
        elif publication_id == latest_id and publication_status == "published":
            status = "current"
        else:
            status = "superseded"

        result["publication_status"] = status
        result["publication"] = ({
            "job_portrait_id": portrait_id,
            "publication_version": version,
            "published_at": published_at,
            "status": status,
        } if status != "unavailable" else None)
        result["publication_snapshot"] = ({
            field: snapshot[field]
            for field in ("position_name", "source_text", "source_uri", "source_fetched_at",
                          "source_license", "required_skills", "job_profile", "field_evidence")
            if field in snapshot
        } if snapshot is not None else None)
        return result

    def get_saved_job_by_record(self, record_id: int, *, user_id: str):
        if not user_id:
            raise PermissionError("user_id required")
        mysql = get_mysql_client()
        rows = mysql.fetch_all(
            self._saved_job_select() + " WHERE sj.user_id=:user_id AND sj.id=:record_id LIMIT 1",
            {"user_id": user_id, "record_id": record_id},
        )
        return self._project_saved_job(rows[0]) if rows else None

    def save_published_job(self, *, user_id: str, job_portrait_id: str,
                           publication_version: str, content_sha256: str) -> int:
        """Bind a save to the current, digest-verified publication only.

        User-visible fields are taken exclusively from the immutable server-side
        snapshot. A unique (owner, publication) key makes repeated saves idempotent.
        """
        if not user_id:
            raise PermissionError("user_id required")
        if job_portrait_id.startswith("source_csb_"):
            raise SavedJobPublicationError("官方来源岗位只供查看，不能收藏为已审核岗位")
        from ..services.job_publication import content_sha256 as digest_json, validate_snapshot

        mysql = get_mysql_client()
        with mysql.engine.begin() as conn:
            rows = conn.execute(text(
                "SELECT id, job_portrait_id, publication_version, snapshot_json, content_sha256, "
                "status, published_at FROM job_catalog_publication "
                "WHERE job_portrait_id=:jid AND publication_version=:version FOR UPDATE"
            ), {"jid": job_portrait_id, "version": publication_version}).mappings().all()
            if not rows:
                raise SavedJobPublicationError("岗位版本已不存在，请返回目录刷新后重试")
            publication = dict(rows[0])
            latest_rows = conn.execute(text(
                "SELECT id, status FROM job_catalog_publication WHERE job_portrait_id=:jid "
                "ORDER BY id DESC LIMIT 1 FOR UPDATE"
            ), {"jid": job_portrait_id}).mappings().all()
            latest = dict(latest_rows[0]) if latest_rows else None
            if publication["status"] != "published" or not latest or latest["id"] != publication["id"]:
                raise SavedJobPublicationError("岗位版本已撤回或被新版本替代，请刷新目录")
            try:
                snapshot = _decoded_json(publication["snapshot_json"])
                validate_snapshot(snapshot)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise SavedJobPublicationError("岗位版本的来源资料暂时无法核验，不能收藏") from exc
            if (snapshot.get("review_state") != "approved"
                    or publication["content_sha256"] != digest_json(snapshot)
                    or publication["content_sha256"] != content_sha256):
                raise SavedJobPublicationError("岗位资料已变化，请刷新目录后再收藏")

            conn.execute(text("""
                INSERT INTO saved_job
                    (user_id, job_id, job_title, company, industry, salary, location,
                     match_score, required_skills, publication_id)
                VALUES (:user_id, :job_id, :job_title, NULL, NULL, NULL, NULL,
                        NULL, :required_skills, :publication_id)
                ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id)
            """), {
                "user_id": user_id,
                "job_id": job_portrait_id,
                "job_title": snapshot["position_name"],
                "required_skills": json.dumps(snapshot["required_skills"], ensure_ascii=False),
                "publication_id": publication["id"],
            })
            saved = conn.execute(text(
                "SELECT id FROM saved_job WHERE user_id=:user_id AND publication_id=:publication_id LIMIT 1"
            ), {"user_id": user_id, "publication_id": publication["id"]}).mappings().first()
            if not saved:
                raise RuntimeError("岗位收藏写入后未找到对应记录")
            return int(saved["id"])

    def save_job(self, *, job_id: str, user_id: Optional[str],
                 job_title: Optional[str], company: Optional[str],
                 industry: Optional[str], salary: Optional[str],
                 location: Optional[str], match_score: float = 0,
                 required_skills: Optional[str] = None) -> int:
        """收藏岗位 (required_skills 应为 JSON 字符串, 由 API 层序列化)"""
        if not user_id:
            raise PermissionError("user_id required")
        mysql = get_mysql_client()
        sql = """
        INSERT INTO saved_job
        (job_id, user_id, job_title, company, industry, salary, location, match_score, required_skills)
        VALUES (:job_id, :user_id, :job_title, :company, :industry, :salary, :location, :match_score, :required_skills)
        """
        params = {
            "job_id": job_id, "user_id": user_id, "job_title": job_title,
            "company": company, "industry": industry, "salary": salary,
            "location": location, "match_score": match_score,
            "required_skills": required_skills,
        }
        with mysql.engine.begin() as conn:
            result = conn.execute(text(sql), params)
            return int(result.lastrowid)

    def delete_saved_job(self, job_id: str, *, user_id: str) -> None:
        """取消收藏"""
        if not user_id:
            raise PermissionError("user_id required")
        mysql = get_mysql_client()
        mysql.execute_write(
            "DELETE FROM saved_job WHERE job_id = :job_id AND user_id = :user_id",
            {"job_id": job_id, "user_id": user_id},
        )

    def delete_saved_job_record(self, record_id: int, *, user_id: str) -> None:
        if not user_id:
            raise PermissionError("user_id required")
        get_mysql_client().execute_write(
            "DELETE FROM saved_job WHERE id=:record_id AND user_id=:user_id",
            {"record_id": record_id, "user_id": user_id},
        )

    # ── 目标职业方向 ──

    def list_target_roles(self, user_id: str) -> List[Dict[str, Any]]:
        """查询当前用户目标职业方向。"""
        if not user_id:
            raise PermissionError("user_id required")
        mysql = get_mysql_client()
        sql = "SELECT * FROM target_role WHERE user_id = :user_id"
        return mysql.execute(sql, {"user_id": user_id})

    def add_target_role(self, *, user_id: Optional[str], role_name: str,
                        priority: int = 1) -> int:
        """添加目标职业方向"""
        if not user_id:
            raise PermissionError("user_id required")
        mysql = get_mysql_client()
        with mysql.engine.begin() as conn:
            result = conn.execute(text(
            "INSERT INTO target_role (user_id, role_name, priority) "
            "VALUES (:user_id, :role_name, :priority)"),
            {"user_id": user_id, "role_name": role_name, "priority": priority},
            )
            return int(result.lastrowid)

    def delete_target_role(self, *, role_name: str, user_id: Optional[str]) -> None:
        """移除目标职业方向"""
        if not user_id:
            raise PermissionError("user_id required")
        mysql = get_mysql_client()
        mysql.execute_write(
            "DELETE FROM target_role WHERE role_name = :role AND user_id = :user_id",
            {"role": role_name, "user_id": user_id},
        )

    def get_role_insight(self, role: str) -> Optional[Dict[str, Any]]:
        """Read only cold-start direction identity/skills, not market claims."""
        mysql = get_mysql_client()
        sql = """
        SELECT role_name, core_skills
        FROM career_insights
        WHERE role_name = :role OR role_name LIKE :role_like
        LIMIT 1
        """
        rows = mysql.execute(sql, {"role": role, "role_like": f"%{role}%"})
        return rows[0] if rows else None


# 模块级单例 (Repository 无状态, 每次方法内获取 mysql_client)
favorites_repo = FavoritesRepository()
