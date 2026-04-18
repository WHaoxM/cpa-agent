"""
Demo Pipeline API - 带 Mock 降级的管线 API
"""

import uuid
from flask import jsonify
from . import pipeline_bp

MOCK_PIPELINES = [
    {'name': 'L0_parse_resume', 'description': '简历解析 (MinerU + YOLOv10 + Qwen3-SFT)'},
    {'name': 'L1_build_graph', 'description': '知识图谱构建 (技能骨架 + 岗位聚合 + 路径图谱)'},
    {'name': 'L2_generate_portrait', 'description': '画像生成 (学生四维画像 + 岗位四维画像)'},
    {'name': 'L3_run_match', 'description': '人岗匹配 (D1硬约束 + D2技能覆盖 + D3/D4加权)'},
    {'name': 'L4_generate_report', 'description': '报告生成 (6章结构, 模板70% + LLM30%)'},
]


@pipeline_bp.route('/list', methods=['GET'])
def list_pipelines():
    """列出所有管线 - Demo 模式"""
    return jsonify({
        'success': True,
        'data': MOCK_PIPELINES
    })


@pipeline_bp.route('/trigger', methods=['POST'])
def trigger_pipeline():
    """触发管线任务 - Demo 模式"""
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    return jsonify({
        'success': True,
        'data': {
            'task_id': task_id,
            'pipeline': 'L0_parse_resume',
            'status': 'completed',
            'mode': 'demo'
        }
    })


@pipeline_bp.route('/status/<task_id>', methods=['GET'])
def get_task_status(task_id: str):
    """获取任务状态 - Demo 模式"""
    return jsonify({
        'success': True,
        'data': {
            'task_id': task_id,
            'pipeline': 'L0_parse_resume',
            'status': 'completed',
            'progress': 100,
            'mode': 'demo'
        }
    })


@pipeline_bp.route('/l0/parse-resume', methods=['POST'])
def parse_resume():
    """L0 简历解析 - Demo 模式"""
    task_id = f"task_{uuid.uuid4().hex[:12]}"
    return jsonify({
        'success': True,
        'data': {
            'task_id': task_id,
            'status': 'completed',
            'message': 'Demo 模式: Mock 简历解析'
        }
    })
