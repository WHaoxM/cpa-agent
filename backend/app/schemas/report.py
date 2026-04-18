"""
报告相关 Schema
"""

from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field


class ReportGenerateRequest(BaseModel):
    """生成报告请求"""
    student_id: str = Field(..., description='学生ID')
    job_portrait_id: str = Field(..., description='岗位画像ID')
    title: Optional[str] = Field(None, description='报告标题')


class ReportChapter(BaseModel):
    """报告章节"""
    title: str
    content: str
    key_points: Optional[List[str]] = None


class ReportResponse(BaseModel):
    """报告响应"""
    report_id: str
    student_id: str
    job_portrait_id: str
    title: Optional[str] = None
    status: str
    chapters: Optional[Dict[str, Any]] = None
    html_path: Optional[str] = None
    pdf_path: Optional[str] = None
    elapsed_seconds: Optional[float] = None
    created_at: Optional[str] = None

    class Config:
        from_attributes = True


class ReportExportRequest(BaseModel):
    """导出报告请求"""
    format: str = Field(default="html", description='导出格式: html/pdf')
