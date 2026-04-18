"""
技能抽取模块 (Skill Extraction)
最小化实现: 预定义词典 + 正则匹配 + 上下文验证
"""

import re
from typing import List, Dict, Set, Tuple
from dataclasses import dataclass


@dataclass
class ExtractedSkill:
    name: str
    category: str
    confidence: float
    source_text: str
    position: int


class SkillDictionary:
    """技能词典 - 扩展版 200+"""

    SKILLS = {
        # ========== 编程语言 ==========
        "Python": "编程语言",
        "Java": "编程语言",
        "JavaScript": "编程语言",
        "TypeScript": "编程语言",
        "C++": "编程语言",
        "C#": "编程语言",
        "Go": "编程语言",
        "Rust": "编程语言",
        "PHP": "编程语言",
        "Ruby": "编程语言",
        "Swift": "编程语言",
        "Kotlin": "编程语言",
        "Scala": "编程语言",
        "R": "编程语言",
        "MATLAB": "编程语言",
        "Perl": "编程语言",
        "Lua": "编程语言",
        "Shell": "编程语言",
        "Bash": "编程语言",
        "PowerShell": "编程语言",
        "Zig": "编程语言",
        "Elixir": "编程语言",
        "Clojure": "编程语言",
        "Haskell": "编程语言",
        "Dart": "编程语言",
        "Groovy": "编程语言",
        "Julia": "编程语言",

        # ========== 前端开发 ==========
        "HTML": "前端",
        "HTML5": "前端",
        "CSS": "前端",
        "CSS3": "前端",
        "Vue.js": "前端",
        "Vue": "前端",
        "Vue 2": "前端",
        "Vue 3": "前端",
        "React": "前端",
        "React.js": "前端",
        "Angular": "前端",
        "AngularJS": "前端",
        "Svelte": "前端",
        "jQuery": "前端",
        "Bootstrap": "前端",
        "Tailwind CSS": "前端",
        "Tailwind": "前端",
        "Element UI": "前端",
        "Ant Design": "前端",
        "Ant Design Pro": "前端",
        "Webpack": "前端",
        "Vite": "前端",
        "Sass": "前端",
        "Scss": "前端",
        "Less": "前端",
        "Node.js": "前端",
        "Node": "前端",
        "Next.js": "前端",
        "Nuxt.js": "前端",
        "Umi.js": "前端",
        "Egg.js": "前端",
        "Remix": "前端",
        "SvelteKit": "前端",

        # ========== 后端框架 ==========
        "Django": "后端框架",
        "Flask": "后端框架",
        "FastAPI": "后端框架",
        "Spring": "后端框架",
        "Spring Boot": "后端框架",
        "Spring Cloud": "后端框架",
        "Spring MVC": "后端框架",
        "MyBatis": "后端框架",
        "Hibernate": "后端框架",
        "Express.js": "后端框架",
        "NestJS": "后端框架",
        "Koa": "后端框架",
        "Ruby on Rails": "后端框架",
        "Rails": "后端框架",
        "Laravel": "后端框架",
        "Gin": "后端框架",
        "Beego": "后端框架",
        "Fiber": "后端框架",
        "Phoenix": "后端框架",
        "Play Framework": "后端框架",
        "Vert.x": "后端框架",
        "Quarkus": "后端框架",

        # ========== 数据库 ==========
        "MySQL": "数据库",
        "PostgreSQL": "数据库",
        "MongoDB": "数据库",
        "Redis": "数据库",
        "Elasticsearch": "数据库",
        "Oracle": "数据库",
        "SQLServer": "数据库",
        "SQL Server": "数据库",
        "SQLite": "数据库",
        "Cassandra": "数据库",
        "DynamoDB": "数据库",
        "Neo4j": "数据库",
        "InfluxDB": "数据库",
        "TimescaleDB": "数据库",
        "MariaDB": "数据库",
        "TiDB": "数据库",
        "OceanBase": "数据库",
        "ClickHouse": "数据库",
        "Hive": "数据库",
        "HBase": "数据库",
        "Memcached": "数据库",
        "CouchDB": "数据库",
        "Couchbase": "数据库",

        # ========== 大数据 ==========
        "Hadoop": "大数据",
        "Spark": "大数据",
        "Flink": "大数据",
        "Kafka": "大数据",
        "Storm": "大数据",
        "Beam": "大数据",
        "DataX": "大数据",
        "Flume": "大数据",
        "Sqoop": "大数据",
        "Azkaban": "大数据",
        "Oozie": "大数据",
        "Doris": "大数据",
        "Presto": "大数据",
        "Trino": "大数据",
        "Kylin": "大数据",
        "Druid": "大数据",

        # ========== AI/ML ==========
        "TensorFlow": "AI/ML",
        "PyTorch": "AI/ML",
        "Keras": "AI/ML",
        "Scikit-learn": "AI/ML",
        "Scikit": "AI/ML",
        "Pandas": "AI/ML",
        "NumPy": "AI/ML",
        "SciPy": "AI/ML",
        "OpenCV": "AI/ML",
        "NLTK": "AI/ML",
        "Hugging Face": "AI/ML",
        "Transformers": "AI/ML",
        "LangChain": "AI/ML",
        "LLaMA": "AI/ML",
        "ChatGLM": "AI/ML",
        "BERT": "AI/ML",
        "GPT": "AI/ML",
        "GPT-4": "AI/ML",
        "LSTM": "AI/ML",
        "CNN": "AI/ML",
        "RNN": "AI/ML",
        "XGBoost": "AI/ML",
        "LightGBM": "AI/ML",
        "CatBoost": "AI/ML",
        "Spark MLlib": "AI/ML",
        "Mahout": "AI/ML",
        "OpenAI": "AI/ML",
        "Midjourney": "AI/ML",
        "Stable Diffusion": "AI/ML",
        "Diffusion Model": "AI/ML",

        # ========== DevOps ==========
        "Docker": "DevOps",
        "Kubernetes": "DevOps",
        "K8s": "DevOps",
        "Jenkins": "DevOps",
        "GitLab CI": "DevOps",
        "GitHub Actions": "DevOps",
        "Travis CI": "DevOps",
        "CircleCI": "DevOps",
        "Ansible": "DevOps",
        "Terraform": "DevOps",
        "Prometheus": "DevOps",
        "Grafana": "DevOps",
        "ELK": "DevOps",
        "Elastic Stack": "DevOps",
        "Kibana": "DevOps",
        "Logstash": "DevOps",
        "Nagios": "DevOps",
        "Zabbix": "DevOps",
        "PagerDuty": "DevOps",
        "Sentry": "DevOps",
        "Datadog": "DevOps",
        "New Relic": "DevOps",
        "Argo CD": "DevOps",
        "Helm": "DevOps",
        "Istio": "DevOps",
        "Envoy": "DevOps",
        "Kong": "DevOps",
        "Nginx": "DevOps",
        "Apache": "DevOps",
        "Tomcat": "DevOps",
        "Jetty": "DevOps",

        # ========== 云计算 ==========
        "AWS": "云计算",
        "Amazon AWS": "云计算",
        "Azure": "云计算",
        "GCP": "云计算",
        "Google Cloud": "云计算",
        "阿里云": "云计算",
        "Alibaba Cloud": "云计算",
        "腾讯云": "云计算",
        "Tencent Cloud": "云计算",
        "华为云": "云计算",
        "Huawei Cloud": "云计算",
        "字节云": "云计算",
        "金山云": "云计算",
        "UCloud": "云计算",
        "青云": "云计算",
        "七牛云": "云计算",
        "OBS": "云计算",
        "OSS": "云计算",
        "COS": "云计算",
        "S3": "云计算",
        "EC2": "云计算",
        "ECS": "云计算",
        "Lambda": "云计算",
        "函数计算": "云计算",
        "Serverless": "云计算",
        "容器服务": "云计算",
        "ACK": "云计算",
        "EKS": "云计算",
        "GKE": "云计算",

        # ========== 版本控制 ==========
        "Git": "工具",
        "SVN": "工具",
        "Mercurial": "工具",
        "GitHub": "工具",
        "GitLab": "工具",
        "Gitee": "工具",
        "Bitbucket": "工具",
        "GitHub Desktop": "工具",
        "Sourcetree": "工具",

        # ========== 项目管理 ==========
        "敏捷": "方法论",
        "Agile": "方法论",
        "Scrum": "方法论",
        "Kanban": "方法论",
        "XP": "方法论",
        "极限编程": "方法论",
        "TDD": "方法论",
        "BDD": "方法论",
        "ATDD": "方法论",
        "JIRA": "工具",
        "Confluence": "工具",
        "禅道": "工具",
        "Teambition": "工具",
        "钉钉": "工具",
        "飞书": "工具",
        "Slack": "工具",
        "Notion": "工具",
        "Figma": "工具",
        "Sketch": "工具",

        # ========== 数据分析 ==========
        "SQL": "数据",
        "NoSQL": "数据",
        "ETL": "数据",
        "数据可视化": "数据",
        "Tableau": "数据",
        "PowerBI": "数据",
        "Power BI": "数据",
        "FineBI": "数据",
        "帆软": "数据",
        "ECharts": "数据",
        "D3.js": "数据",
        "Apache ECharts": "数据",
        "Looker": "数据",
        "Metabase": "数据",
        "Superset": "数据",
        "Airflow": "数据",
        "Dagster": "数据",
        "Kettle": "数据",
        "DataWorks": "数据",

        # ========== 操作系统 ==========
        "Linux": "操作系统",
        "Windows": "操作系统",
        "macOS": "操作系统",
        "Unix": "操作系统",
        "CentOS": "操作系统",
        "Ubuntu": "操作系统",
        "Debian": "操作系统",
        "Red Hat": "操作系统",
        "RHEL": "操作系统",
        "Fedora": "操作系统",
        "Kali": "操作系统",
        "Windows Server": "操作系统",
        "iOS": "操作系统",
        "Android": "操作系统",
        "HarmonyOS": "操作系统",
        "鸿蒙": "操作系统",

        # ========== 网络安全 ==========
        "网络安全": "安全",
        "Web安全": "安全",
        "XSS": "安全",
        "SQL注入": "安全",
        "CSRF": "安全",
        "OAuth": "安全",
        "JWT": "安全",
        "SSL": "安全",
        "TLS": "安全",
        "HTTPS": "安全",
        "防火墙": "安全",
        "WAF": "安全",
        "DDoS": "安全",
        "渗透测试": "安全",
        "Kali Linux": "安全",
        "Burp Suite": "安全",
        "OWASP": "安全",
        "等级保护": "安全",
        "安全加固": "安全",
        "威胁情报": "安全",
        "零信任": "安全",

        # ========== 嵌入式/物联网 ==========
        "嵌入式": "嵌入式",
        "RTOS": "嵌入式",
        "FreeRTOS": "嵌入式",
        "Arduino": "嵌入式",
        "STM32": "嵌入式",
        "单片机": "嵌入式",
        "MCU": "嵌入式",
        "ARM": "嵌入式",
        "Raspberry Pi": "嵌入式",
        "树莓派": "嵌入式",
        "IoT": "嵌入式",
        "物联网": "嵌入式",
        "MQTT": "嵌入式",
        "CoAP": "嵌入式",
        "NB-IoT": "嵌入式",
        "LoRa": "嵌入式",
        "ZigBee": "嵌入式",
        "Modbus": "嵌入式",
        "PLC": "嵌入式",
        "SCADA": "嵌入式",
    }

    ALIASES = {
        "JS": "JavaScript",
        "TS": "TypeScript",
        "Py": "Python",
        "VUE": "Vue.js",
        "K8S": "Kubernetes",
        "ML": "机器学习",
        "DL": "深度学习",
        "NLP": "自然语言处理",
        "CV": "计算机视觉",
        "FE": "前端",
        "BE": "后端",
        "FS": "全栈",
        "DBA": "数据库工程师",
        "RD": "研发工程师",
        "QA": "测试工程师",
        "OP": "运维工程师",
        "SRE": "SRE",
        "DevOps": "DevOps",
        "SSH": "远程连接",
        "REST": "RESTful",
        "RESTful": "RESTful",
        "API": "API",
        "CRUD": "CRUD",
        "ORM": "ORM",
        "AOP": "AOP",
        "微服务": "微服务",
        "SOA": "微服务",
        "云原生": "云原生",
        "CNCF": "云原生",
        "Service Mesh": "Service Mesh",
        "CI/CD": "CI/CD",
        "敏捷开发": "敏捷",
        "重构": "重构",
        "设计模式": "设计模式",
        "架构": "架构设计",
        "分布式": "分布式",
        "高并发": "高并发",
        "负载均衡": "负载均衡",
        "缓存": "缓存",
        "消息队列": "消息队列",
        "异步": "异步",
        "并行": "并行",
        "多线程": "多线程",
        "协程": "协程",
        "容器化": "Docker",
        "虚拟化": "虚拟化",
        "K8S": "Kubernetes",
    }


class SkillExtractor:
    """技能抽取器"""

    def __init__(self):
        self.dictionary = SkillDictionary()
        self._build_patterns()

    def _build_patterns(self):
        """构建匹配模式"""
        all_skills = list(self.dictionary.SKILLS.keys()) + list(self.dictionary.ALIASES.keys())
        sorted_skills = sorted(all_skills, key=len, reverse=True)

        skill_pattern = "|".join(re.escape(s) for s in sorted_skills)
        self.skill_regex = re.compile(skill_pattern, re.IGNORECASE)

        self.context_negative = [
            r"了解", r"熟悉", r"掌握", r"精通",
            r"使用过", r"学习过", r"接触过",
            r"曾经", r"以前", r"用过",
        ]

        self.context_positive = [
            r"熟练", r"精通", r"负责", r"主导",
            r"多年", r"深入", r"专家",
        ]

    def extract(self, text: str) -> List[ExtractedSkill]:
        """从文本中抽取技能"""
        if not text:
            return []

        skills = []
        seen = set()

        for match in self.skill_regex.finditer(text):
            skill_raw = match.group()
            skill_name = self.dictionary.ALIASES.get(skill_raw, skill_raw)

            if skill_name in seen:
                continue
            seen.add(skill_name)

            confidence = self._calculate_confidence(text, match.start(), skill_name)

            category = self._get_category(skill_name)

            skills.append(ExtractedSkill(
                name=skill_name,
                category=category,
                confidence=confidence,
                source_text=self._get_context(text, match.start(), 50),
                position=match.start()
            ))

        skills.sort(key=lambda x: x.confidence, reverse=True)
        return skills

    def _calculate_confidence(self, text: str, position: int, skill_name: str) -> float:
        """计算技能提及置信度"""
        base_confidence = 0.5

        context_window = text[max(0, position-100):position+100].lower()

        positive_count = sum(1 for p in self.context_positive if p in context_window)
        negative_count = sum(1 for p in self.context_negative if p in context_window)

        confidence = base_confidence + (positive_count * 0.15) - (negative_count * 0.1)
        confidence = max(0.3, min(0.95, confidence))

        if skill_name in self.dictionary.SKILLS:
            confidence += 0.1

        return round(confidence, 2)

    def _get_category(self, skill_name: str) -> str:
        """获取技能类别"""
        return self.dictionary.SKILLS.get(skill_name, "其他")

    def _get_context(self, text: str, position: int, window: int) -> str:
        """获取技能出现的上下文"""
        start = max(0, position - window)
        end = min(len(text), position + window)
        return text[start:end].strip()


def extract_skills(text: str) -> List[Dict]:
    """
    便捷函数: 从文本中抽取技能

    Args:
        text: 输入文本(简历、JD等)

    Returns:
        技能列表,每项包含 name, category, confidence
    """
    extractor = SkillExtractor()
    skills = extractor.extract(text)

    return [
        {
            "name": s.name,
            "category": s.category,
            "confidence": s.confidence,
        }
        for s in skills
    ]


def extract_skills_with_context(text: str) -> List[Dict]:
    """抽取技能并返回上下文"""
    extractor = SkillExtractor()
    skills = extractor.extract(text)

    return [
        {
            "name": s.name,
            "category": s.category,
            "confidence": s.confidence,
            "context": s.source_text,
        }
        for s in skills
    ]


if __name__ == "__main__":
    test_text = """
    Python 后端开发工程师

    技能要求:
    - 熟练掌握 Python、Django、Flask
    - 熟悉 MySQL、Redis 数据库
    - 了解 Docker、Kubernetes
    - 3年以上后端开发经验

    加分项:
    - 有 Vue.js 或 React 开发经验
    - 熟悉 Git、Jenkins
    - 了解大数据技术如 Hadoop、Spark
    """

    skills = extract_skills_with_context(test_text)

    print("=" * 50)
    print("技能抽取结果")
    print("=" * 50)

    for skill in skills:
        print(f"\n[{skill['category']}] {skill['name']} (置信度: {skill['confidence']})")
        print(f"  上下文: ...{skill['context']}...")
