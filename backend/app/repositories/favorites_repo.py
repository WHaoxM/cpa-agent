"""
收藏与目标岗位数据访问 (Repository)
集中 favorites 相关 SQL, 与 API 层解耦
"""

from typing import Optional, List, Dict, Any

from ..services.db import get_mysql_client
from ..utils.logger import get_logger

logger = get_logger('cpa-agent.repositories.favorites')


class FavoritesRepository:
    """收藏与目标岗位数据访问"""

    # ── 收藏岗位 ──

    def list_saved_jobs(self, user_id: str) -> List[Dict[str, Any]]:
        """查询当前用户收藏岗位。"""
        if not user_id:
            raise PermissionError("user_id required")
        mysql = get_mysql_client()
        sql = "SELECT * FROM saved_job WHERE user_id = :user_id ORDER BY saved_at DESC"
        return mysql.execute(sql, {"user_id": user_id})

    def save_job(self, *, job_id: str, user_id: Optional[str],
                 job_title: Optional[str], company: Optional[str],
                 industry: Optional[str], salary: Optional[str],
                 location: Optional[str], match_score: float = 0,
                 required_skills: Optional[str] = None) -> None:
        """收藏岗位 (required_skills 应为 JSON 字符串, 由 API 层序列化)"""
        mysql = get_mysql_client()
        sql = """
        INSERT INTO saved_job
        (job_id, user_id, job_title, company, industry, salary, location, match_score, required_skills)
        VALUES (:job_id, :user_id, :job_title, :company, :industry, :salary, :location, :match_score, :required_skills)
        """
        mysql.execute_write(sql, {
            "job_id": job_id, "user_id": user_id, "job_title": job_title,
            "company": company, "industry": industry, "salary": salary,
            "location": location, "match_score": match_score,
            "required_skills": required_skills,
        })

    def delete_saved_job(self, job_id: str, *, user_id: str) -> None:
        """取消收藏"""
        if not user_id:
            raise PermissionError("user_id required")
        mysql = get_mysql_client()
        mysql.execute_write(
            "DELETE FROM saved_job WHERE job_id = :job_id AND user_id = :user_id",
            {"job_id": job_id, "user_id": user_id},
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
                        priority: int = 1) -> None:
        """添加目标职业方向"""
        mysql = get_mysql_client()
        mysql.execute_write(
            "INSERT INTO target_role (user_id, role_name, priority) "
            "VALUES (:user_id, :role_name, :priority)",
            {"user_id": user_id, "role_name": role_name, "priority": priority},
        )

    def delete_target_role(self, *, role_name: str, user_id: Optional[str]) -> None:
        """移除目标职业方向"""
        mysql = get_mysql_client()
        mysql.execute_write(
            "DELETE FROM target_role WHERE role_name = :role AND user_id = :user_id",
            {"role": role_name, "user_id": user_id},
        )

    # ── 岗位市场统计 (跨表查询 job_posting) ──

    def get_role_market_stats(self, role: str) -> Optional[Dict[str, Any]]:
        """查询岗位市场统计 (薪资/需求数), 无匹配返回 None

        返回: {role, avg_salary_min, avg_salary_max, demand_count} 或 None
        """
        mysql = get_mysql_client()
        sql = """
        SELECT
            position_name as role,
            AVG(salary_min) as avg_salary_min,
            AVG(salary_max) as avg_salary_max,
            COUNT(*) as demand_count
        FROM job_posting
        WHERE position_name LIKE :role AND is_it_position = 1
        GROUP BY position_name
        """
        results = mysql.execute(sql, {"role": f"%{role}%"})
        return results[0] if results else None

    def get_role_insight(self, role: str) -> Optional[Dict[str, Any]]:
        """从 career_insights 取热门城市/技能等，补齐市场卡片。"""
        mysql = get_mysql_client()
        sql = """
        SELECT role_name, avg_salary_low, avg_salary_high, demand_level,
               hot_cities, hot_industries, core_skills, description
        FROM career_insights
        WHERE role_name = :role OR role_name LIKE :role_like
        LIMIT 1
        """
        rows = mysql.execute(sql, {"role": role, "role_like": f"%{role}%"})
        return rows[0] if rows else None


# 模块级单例 (Repository 无状态, 每次方法内获取 mysql_client)
favorites_repo = FavoritesRepository()
