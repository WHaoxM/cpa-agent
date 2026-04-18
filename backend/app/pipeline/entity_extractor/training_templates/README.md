# SFT训练数据模板

## 目录结构

```
training_templates/
├── README.md              # 本文件
├── sft_template.py        # 训练模板定义（核心）
├── data_validator.py      # 数据验证工具
├── convert_to_training.py # 数据格式转换工具
└── training_example.py    # 训练示例（待添加）
```

## 快速开始

### 1. 生成训练数据

```python
from sft_template import RESUME_TEMPLATE, JD_TEMPLATE

# 使用模板生成训练样本
resume_sample = {
    "messages": [
        {"role": "system", "content": RESUME_TEMPLATE["messages"][0]["content"]},
        {"role": "user", "content": "请从以下简历文本中抽取信息：\n\n钟梓珉，大模型应用/Agent开发工程师..."},
        {"role": "assistant", "content": '{"person_name": "钟梓珉", ...}'}
    ]
}
```

### 2. 验证数据格式

```python
from data_validator import TrainingDataValidator, DatasetSplitter

# 验证数据
validator = TrainingDataValidator()
result = validator.validate_dataset(training_data)
print(f"验证通过率: {result['valid_rate']:.2%}")

# 划分数据集
splitter = DatasetSplitter()
splits = splitter.split(data, train_ratio=0.8, val_ratio=0.1, test_ratio=0.1)
```

### 3. 转换现有数据

```bash
# 将NSLE JSON转换为SFT格式
python convert_to_training.py \
    ../entity_extraction_result_nsle.json \
    ./training_data/resume_sft_data.jsonl
```

## 模板说明

### 模板1: 简历实体提取 (RESUME_TEMPLATE)

**用途**: 训练模型从简历文本中抽取结构化信息

**抽取字段**:
- `person_name`: 姓名
- `position`: 求职意向/职位
- `skills`: 技能列表（含等级：了解/熟悉/精通）
- `education`: 教育经历
- `work_experience`: 工作经历
- `project_experience`: 项目经历
- `certificates`: 证书

### 模板2: 岗位描述实体提取 (JD_TEMPLATE)

**用途**: 训练模型从岗位描述中抽取岗位要求

**抽取字段**:
- `job_title`: 岗位名称
- `required_skills`: 技能要求（含等级）
- `education_requirements`: 学历要求
- `experience_requirements`: 经验要求
- `soft_skills`: 软技能
- `growth_potential`: 发展潜力

### 模板3: 人岗匹配分析 (MATCHING_TEMPLATE)

**用途**: 训练模型进行人岗匹配分析

**输出字段**:
- `overall_score`: 综合评分
- `skill_match_rate`: 技能匹配率
- `education_match`: 学历符合度
- `experience_match`: 经验匹配度
- `recommendation`: 推荐建议

### 模板4: 能力画像生成 (PROFILE_TEMPLATE)

**用途**: 训练模型生成候选人能力画像

**输出字段**:
- `technical_stack`: 技术栈分布
- `experience_summary`: 经验概要
- `competency_radar`: 能力雷达图数据
- `strengths/weaknesses`: 优劣势分析

### 模板5: 职业路径规划 (CAREER_PATH_TEMPLATE)

**用途**: 训练模型规划职业发展路径

**输出字段**:
- `short_term_plan`: 短期计划（1年）
- `medium_term_plan`: 中期计划（3年）
- `long_term_plan`: 长期计划（5年）
- `skill_gap_analysis`: 技能差距分析

## 数据格式

### 标准SFT格式 (messages)

```json
{
  "messages": [
    {"role": "system", "content": "你是一个xxx专家..."},
    {"role": "user", "content": "请从以下文本中抽取..."},
    {"role": "assistant", "content": "{\"field1\": \"value1\", ...}"}
  ]
}
```

### JSONL格式

每行一个完整的JSON对象，适合大规模数据存储。

## 训练建议

### 数据量

| 任务类型 | 最低数据量 | 推荐数据量 |
|---------|-----------|-----------|
| 简历抽取 | 200条 | 500-1000条 |
| JD抽取 | 200条 | 500-1000条 |
| 人岗匹配 | 300对 | 500-1000对 |
| 画像生成 | 200条 | 500-1000条 |

### 训练参数 (LoRA)

```python
lora_config = {
    "r": 8,
    "lora_alpha": 16,
    "target_modules": ["q_proj", "k_proj", "v_proj"],
    "lora_dropout": 0.05,
    "bias": "none",
    "task_type": "CAUSAL_LM"
}

training_args = {
    "per_device_train_batch_size": 4,
    "gradient_accumulation_steps": 4,
    "learning_rate": 2e-4,
    "num_train_epochs": 3,
    "warmup_ratio": 0.1
}
```

## 扩展模板

如需添加新的任务类型，请参考 `sft_template.py` 中的模板结构：

1. 定义 `SYSTEM_PROMPT` - 包含任务说明和输出格式
2. 定义 `USER_TEMPLATE` - 用户输入模板
3. 定义 `RESPONSE_TEMPLATE` - 响应输出模板
4. 使用 `format_xxx()` 函数辅助格式化
