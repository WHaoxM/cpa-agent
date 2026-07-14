# JD Cleaner 工具使用说明

## 概述

`jd_cleaner` 是一个集成到 CPA-Agent ReAct 智能体中的工具，用于清洗岗位数据、筛选 IT 相关岗位并生成结构化知识图谱。

## 功能特点

1. **严格的 IT 岗位筛选**：使用强化的正则表达式和 LLM 双重过滤
2. **实时进度显示**：显示处理统计和进度条
3. **断点续传**：支持从中断点继续处理
4. **智能体集成**：可被 ReactAgent 自主调用

## 工具参数

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `action` | string | 是 | 操作类型: `run` / `reset` / `status` |
| `input_file` | string | 否 | 输入文件路径 (默认: `cleaning_data/jd_data.json`) |
| `output_file` | string | 否 | 输出文件路径 (默认: `cleaning_data/structured_jd_knowledge_base.json`) |

## 操作类型

### 1. `status` - 查询状态

查询当前清洗数据的状态。

**调用示例：**
```xml
<tool_call>
{"name": "jd_cleaner", "parameters": {"action": "status"}}
</tool_call>
```

**返回示例：**
```
原始数据: 5000 条
已处理: 1200 条
最终IT岗位: 800 条
```

### 2. `reset` - 重置断点

删除断点文件，以便重新开始清洗。

**调用示例：**
```xml
<tool_call>
{"name": "jd_cleaner", "parameters": {"action": "reset"}}
</tool_call>
```

**返回示例：**
```
断点文件已删除，可重新开始
```

### 3. `run` - 执行清洗

运行完整的清洗流程。

**调用示例：**
```xml
<tool_call>
{"name": "jd_cleaner", "parameters": {"action": "run"}}
</tool_call>
```

**自定义输入/输出文件：**
```xml
<tool_call>
{"name": "jd_cleaner", "parameters": {
  "action": "run",
  "input_file": "cleaning_data/my_jd_data.json",
  "output_file": "cleaning_data/my_output.json"
}}
</tool_call>
```

**返回示例：**
```
[jd_cleaner] 清洗完成！
最终IT岗位: 800 条
输出文件: cleaning_data/structured_jd_knowledge_base.json
```

## 智能体对话示例

### 示例 1: 查询状态

**用户：** 查看一下岗位数据的处理状态

**智能体：**
```
让我查询一下当前的清洗状态。
<tool_call>
{"name": "jd_cleaner", "parameters": {"action": "status"}}
</tool_call>
```

**工具返回：**
```
原始数据: 5000 条
已处理: 1200 条
最终IT岗位: 800 条
```

**智能体（Final Answer）：**
```
当前岗位数据处理状态：
- 原始数据: 5000 条
- 已处理: 1200 条
- 最终IT岗位: 800 条

你可以继续处理剩余数据，或者重新开始清洗。
```

### 示例 2: 执行清洗

**用户：** 帮我清洗一下岗位数据，筛选出 IT 相关岗位

**智能体：**
```
好的，我来帮你执行岗位数据清洗任务。这个任务会：
1. 从 jd_data.json 读取原始岗位数据
2. 使用规则预筛选过滤非IT岗位
3. 通过 LLM 进行深度行业判定和维度对齐
4. 生成结构化的 IT 岗位知识库

现在开始执行：
<tool_call>
{"name": "jd_cleaner", "parameters": {"action": "run"}}
</tool_call>
```

**工具返回：**
```
[jd_cleaner] 清洗完成！
最终IT岗位: 800 条
输出文件: cleaning_data/structured_jd_knowledge_base.json
```

**智能体（Final Answer）：**
```
Final Answer: 岗位数据清洗完成！

处理结果：
- 最终 IT 岗位: 800 条
- 输出文件: cleaning_data/structured_jd_knowledge_base.json

清洗过程包括：
1. 严格的预筛选，过滤掉非IT岗位
2. LLM 深度行业判定，置信度阈值 0.75
3. 四维画像对齐（基础、技能、素养、潜力）
4. 实时进度统计和断点续传

生成的知识库可以用于后续的人岗匹配分析！
```

### 示例 3: 重置并重新清洗

**用户：** 我想重新处理所有岗位数据，帮我重置一下

**智能体：**
```
我来帮你重置清洗状态，删除断点文件，然后重新开始处理。
<tool_call>
{"name": "jd_cleaner", "parameters": {"action": "reset"}}
</tool_call>
```

**工具返回：**
```
断点文件已删除，可重新开始
```

**智能体：**
```
好的，断点已重置。现在开始重新清洗：
<tool_call>
{"name": "jd_cleaner", "parameters": {"action": "run"}}
</tool_call>
```

## 文件结构

```
cpa-agent/
├── backend/
│   └── app/
│       └── services/
│           ├── react_agent.py          # 已集成 jd_cleaner
│           └── tools/
│               ├── __init__.py         # 已注册 JdCleanerTool
│               ├── base.py             # 工具基类
│               └── jd_cleaner.py       # 新工具
└── cleaning_data/
    ├── __init__.py
    ├── p1_LLM.py                      # 核心清洗脚本
    ├── jd_data.json                    # 输入数据
    ├── structured_jd_checkpoint.jsonl # 断点文件
    └── structured_jd_knowledge_base.json # 输出文件
```

## 技术细节

### 预筛选规则
- 使用负向前瞻正则排除非技术岗位（如"软件事业处"）
- 严格黑名单：保安、保洁、厨师、普工等传统岗位
- IT 关键词验证：软件、互联网、计算机、IT、数据、AI、算法等

### LLM 质量闸门
- 置信度阈值：0.75
- 维度完整性：至少一个维度有有效条目
- 行业判定：必须明确为计算机/信息化相关

### 统计指标
- 总数、预筛通过、预筛拒绝
- LLM 处理、LLM 通过、LLM 拒绝
- 错误数、耗时

## 注意事项

1. **API Key**: 确保 `DEEPSEEK_API_KEY` 环境变量已设置
2. **并发数**: 默认 `MAX_WORKERS = 8`，可在 `p1_LLM.py` 中调整
3. **断点续传**: 已处理的岗位会自动跳过，不会重复处理
4. **线程安全**: 使用 `threading.Lock` 保证文件写入和统计更新的线程安全