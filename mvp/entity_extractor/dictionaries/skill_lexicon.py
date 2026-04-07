"""
技能词典 — 6大类结构化组织

供 AC 自动机（pyahocorasick）按类别权重匹配。
单一数据源，替代散落在 phase2/hybrid_pipeline/skill_matcher 中的重复词表。
"""

from __future__ import annotations

from typing import Dict, List

# ---------------------------------------------------------------------------
# 6 大类技能词典
# ---------------------------------------------------------------------------

SKILL_CATEGORIES: Dict[str, List[str]] = {
    "programming_languages": [
        "Python", "Java", "JavaScript", "TypeScript", "Go", "Rust",
        "C++", "C#", "C", "Kotlin", "Scala", "Ruby", "PHP", "Swift",
        "Dart", "Lua", "R", "MATLAB", "Shell", "Bash",
    ],
    "web_frameworks": [
        "FastAPI", "Flask", "Django", "Spring Boot", "Spring", "Express",
        "Vue", "React", "Angular", "Next.js", "Nuxt.js", "Svelte",
        "Gin", "Echo", "Fiber", "Actix", "Rocket",
        "REST API", "GraphQL", "WebSocket", "gRPC",
    ],
    "ai_ml": [
        "LangChain", "LangGraph", "LlamaIndex", "RAG", "Agent", "多Agent",
        "AutoGen", "CrewAI", "MetaGPT", "OpenAgents",
        "PyTorch", "TensorFlow", "Keras", "Hugging Face", "Transformers",
        "BERT", "GPT", "Qwen", "LLaMA", "ChatGLM",
        "ONNX", "vLLM", "Ollama", "OpenAI",
        "强化学习", "深度学习", "机器学习", "自然语言处理",
        "计算机视觉", "多模态", "Prompt Engineering",
        "Fine-tuning", "SFT", "RLHF", "LoRA", "QLoRA",
    ],
    "databases": [
        "MySQL", "PostgreSQL", "MongoDB", "Neo4j", "Milvus", "Redis",
        "Elasticsearch", "SQLite", "Oracle", "SQL Server",
        "Pinecone", "Weaviate", "Chroma", "FAISS",
        "知识图谱", "向量数据库", "向量检索", "图数据库",
    ],
    "devops_tools": [
        "Docker", "Kubernetes", "Jenkins", "GitHub", "GitLab", "Git",
        "CI/CD", "Nginx", "Linux", "AWS", "Azure", "GCP",
        "Prometheus", "Grafana", "ELK",
        "Microservices", "DevOps",
    ],
    "concepts": [
        "NLP", "CV", "OCR", "ASR", "TTS",
        "数据挖掘", "推荐系统", "搜索引擎",
        "分布式系统", "高并发", "消息队列",
        "Kafka", "RabbitMQ", "ZooKeeper",
        "设计模式", "领域驱动设计", "微服务架构",
    ],
    "soft_skills": [
        "团队合作", "数据分析", "文献检索", "论文写作",
        "项目管理", "沟通协调", "问题解决", "学习能力",
        "实验设计", "学术写作", "报告撰写",
        "需求分析", "技术文档", "代码审查", "敏捷开发",
        "跨团队协作", "技术分享", "创新思维", "逻辑思维",
    ],
}


def get_all_skills() -> List[str]:
    """获取所有技能的扁平列表（去重）"""
    seen = set()
    result: List[str] = []
    for skills in SKILL_CATEGORIES.values():
        for s in skills:
            if s not in seen:
                seen.add(s)
                result.append(s)
    return result


def get_skills_by_category(category: str) -> List[str]:
    """按类别获取技能列表"""
    return SKILL_CATEGORIES.get(category, [])


def get_category_for_skill(skill: str) -> str:
    """查找技能所属类别"""
    skill_lower = skill.lower()
    for cat, skills in SKILL_CATEGORIES.items():
        if any(s.lower() == skill_lower for s in skills):
            return cat
    return "unknown"
