"""
Pipeline_ext Normalizer 子模块

实体归一化 API (5 路由):
- POST /normalizer/init          初始化规范实体表 + 种子数据
- POST /normalizer/normalize     归一化实体
- POST /normalizer/normalize/batch  批量归一化
- GET  /normalizer/entities      查询规范实体
- GET  /normalizer/categories    查询实体分类

依赖: ..services.entity_normalizer
"""

from flask import request, jsonify

from ._helpers import safe_error
from .pipeline_ext_common import pipeline_ext_bp, logger


@pipeline_ext_bp.route('/normalizer/init', methods=['POST'])
def init_normalizer():
    """
    初始化规范实体表 + 种子数据

    Body:
        { "seed": true }
    """
    try:
        from ..services.entity_normalizer import init_canonical_tables, seed_canonical_entities
        init_canonical_tables()

        data = request.get_json() or {}
        if data.get("seed", False):
            seed_canonical_entities()

        return jsonify({
            "success": True,
            "data": {"message": "canonical_entity 表已初始化" + (" + 种子数据" if data.get("seed") else "")}
        })
    except Exception as e:
        return jsonify(safe_error("初始化归一化表", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/normalizer/normalize', methods=['POST'])
def normalize_entity():
    """
    归一化实体

    Body:
        {
            "name": "K8S",
            "entity_type": "skill",       // skill/position/major/certificate
            "context": "...",             // 可选
            "auto_register": true         // OOV 是否自动注册
        }
    """
    data = request.get_json() or {}
    name = data.get('name', '')
    entity_type = data.get('entity_type', 'skill')
    context = data.get('context')
    auto_register = data.get('auto_register', True)

    if not name:
        return jsonify({"success": False, "error": "name 不能为空"}), 400

    try:
        from ..services.entity_normalizer import get_entity_normalizer
        normalizer = get_entity_normalizer()
        result = normalizer.normalize(name, entity_type, context, auto_register)

        return jsonify({
            "success": True,
            "data": result.to_dict()
        })
    except Exception as e:
        return jsonify(safe_error("实体归一化", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/normalizer/normalize/batch', methods=['POST'])
def normalize_entities_batch():
    """
    批量归一化

    Body:
        {
            "entities": [{"name": "...", ...}, ...],
            "entity_type": "skill",
            "context": "..."
        }
    """
    data = request.get_json() or {}
    entities = data.get('entities', [])
    entity_type = data.get('entity_type', 'skill')
    context = data.get('context')

    if not entities:
        return jsonify({"success": False, "error": "entities 不能为空"}), 400

    try:
        from ..services.entity_normalizer import get_entity_normalizer
        normalizer = get_entity_normalizer()
        results = normalizer.normalize_batch(entities, entity_type, context)

        return jsonify({
            "success": True,
            "data": {
                "total": len(results),
                "normalized": results,
                "oov_count": sum(1 for r in results if r.get("is_oov")),
            }
        })
    except Exception as e:
        return jsonify(safe_error("批量归一化", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/normalizer/entities', methods=['GET'])
def list_canonical_entities():
    """
    查询规范实体

    Query params:
        entity_type: skill/position/major/certificate
        category: 分类过滤 (可选)
    """
    entity_type = request.args.get('entity_type', 'skill')
    category = request.args.get('category')

    try:
        from ..services.entity_normalizer import get_entity_normalizer
        normalizer = get_entity_normalizer()

        entities = normalizer.get_canonical_entities(entity_type)
        if category:
            entities = [e for e in entities if e.get("category") == category]

        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "count": len(entities),
                "entities": entities[:100],
            }
        })
    except Exception as e:
        return jsonify(safe_error("查询规范实体", e, message="服务器内部错误")), 500


@pipeline_ext_bp.route('/normalizer/categories', methods=['GET'])
def list_categories():
    """
    查询实体分类

    Query params:
        entity_type: skill/position/major/certificate
    """
    entity_type = request.args.get('entity_type', 'skill')

    try:
        from ..services.entity_normalizer import get_entity_normalizer
        normalizer = get_entity_normalizer()
        categories = normalizer.get_categories(entity_type)

        return jsonify({
            "success": True,
            "data": {
                "entity_type": entity_type,
                "categories": categories,
            }
        })
    except Exception as e:
        return jsonify(safe_error("查询分类", e, message="服务器内部错误")), 500
