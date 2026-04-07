# NS-LE Phase 1 真实处理流程图

## 基于实际代码和数据分析的真实流程

```mermaid
flowchart TD
    subgraph INPUT["📥 输入层 - PDF文档解析"]
        A[PDF文件] --> B[MinerU v1/v2/middle 三选一]
    end

    subgraph V1_PATH["V1格式处理 (content_list.json)"]
        V1A[扁平text数组<br/>每个元素独立] --> V1B{手动检查}
        V1B -->|需要| V1C[v2_to_document_blocks<br/>按标题分块]
        V1B -->|不需要| V1D[直接使用扁平列表]
    end

    subgraph V2_PATH["V2格式处理 (content_list_v2.json) - 主流"]
        V2A[嵌套结构<br/>type: title/paragraph] --> V2B{flatten处理}
        V2B --> V2C[二维数组转一维]
        V2C --> V2D[parse_mineru_v2_item_text<br/>提取文本内容]
        V2D --> V2E[v2_to_document_blocks<br/>核心分块逻辑]
    end

    subgraph MIDDLE_PATH["Middle格式处理 (middle.json)"]
        M1[最详细结构<br/>pdf_info→blocks→lines→spans] --> M2[parse_mineru_middle_blocks]
        M2 --> M3[提取text/score/bbox/level]
        M3 --> M4{是否需要精确定位？}
        M4 -->|是| M5[保留lines/spans信息]
        M4 -->|否| M6[简化为block]
    end

    subgraph BLOCK_CLASSIFY["块分类 - 三级分类策略"]
        C1[Pass 1: 章节标签匹配<br/>正则: 教育经历|项目经验|... ]
        C1 --> C2{匹配成功？}
        C2 -->|是| C3[设置block_type<br/>如EDUCATION]
        C2 -->|否| C4[Pass 2: 实体名标题分类<br/>heading+content双信号]
        C4 --> C5{匹配成功？}
        C5 -->|是| C6[设置block_type<br/>如PROJECT_EXPERIENCE]
        C5 -->|否| C7[Pass 3: Section继承<br/>继承上方章节类型]
        C7 --> C8{继承成功？}
        C8 -->|是| C9[设置block_type<br/>继承自current_scope]
        C8 -->|否| C10[Pass 4: Content fallback<br/>正则匹配关键词]
        C10 --> C11[最终分类结果]
    end

    subgraph SEMANTIC_SPLIT["语义二次拆分 - 可选"]
        S1[检测角色+时间戳组合]
        S1 --> S2{找到切割点？}
        S2 -->|是| S3{语义连贯？}
        S3 -->|是| S4[拆分为多个子块]
        S3 -->|否| S5[保留原块]
        S2 -->|否| S5
    end

    subgraph OUTPUT["📤 输出 - DocumentBlocks"]
        O1[block_id: b_1, b_2...]
        O1 --> O2[block_type: 9类之一]
        O2 --> O3[text: 合并后的文本]
        O3 --> O4[confidence: 0.0-1.0]
        O4 --> O5[heading: 所属标题]
        O5 --> O6[source: file_type/page/bbox]
    end

    %% 连接关系
    B -->|V1格式| V1_PATH
    B -->|V2格式 - 主流| V2_PATH
    B -->|Middle格式| MIDDLE_PATH

    V1C --> BLOCK_CLASSIFY
    V2E --> BLOCK_CLASSIFY
    M5 --> BLOCK_CLASSIFY
    M6 --> BLOCK_CLASSIFY

    C3 --> SEMANTIC_SPLIT
    C6 --> SEMANTIC_SPLIT
    C9 --> SEMANTIC_SPLIT
    C11 --> SEMANTIC_SPLIT

    SEMANTIC_SPLIT --> OUTPUT

    %% 样式定义
    classDef input fill:#e1f5fe,stroke:#01579b,stroke-width:3px
    classDef process fill:#f3e5f5,stroke:#4a148c,stroke-width:2px
    classDef decision fill:#fff3e0,stroke:#e65100,stroke-width:2px
    classDef output fill:#e8f5e8,stroke:#1b5e20,stroke-width:2px
    classDef main fill:#e3f2fd,stroke:#0d47a1,stroke-width:3px

    class B,V2_PATH,MIDDLE_PATH,V1_PATH main
    class A,V1A,V2A,M1 input
    class C1,C4,C7,C10,S1 process
    class C2,C4,C5,C8,S2,S3 decision
    class OUTPUT output
```

## 关键问题解答

### ❓ 为什么需要3种格式？

| 格式 | 用途 | 场景 |
|------|------|------|
| **V1** | 快速文本提取 | 简单场景，不需要语义标签 |
| **V2** | **主流格式** | 需要区分标题/段落的结构化处理 |
| **Middle** | 精确定位+版面分析 | 需要bbox精确度或OCR校正 |

### ❓ 基础块分割到底做什么？

**不是简单的按行分割**，而是**按标题分组成块**：

```python
# V2原始数据（扁平列表）：
[
    {"type": "title", "text": "专业技能"},
    {"type": "paragraph", "text": "熟悉 Python..."},
    {"type": "paragraph", "text": "熟悉 FastAPI..."},
    {"type": "title", "text": "项目经历"},
    {"type": "paragraph", "text": "电商识别系统..."},
]

# 分块后（合并为一个block）：
[
    {
        "heading": "专业技能",
        "text": "熟悉 Python...\n熟悉 FastAPI...",
        "block_type": "SKILLS_CERTS"
    },
    {
        "heading": "项目经历",
        "text": "电商识别系统...",
        "block_type": "PROJECT_EXPERIENCE"
    }
]
```

### ❓ 三级分类策略的实际效果

基于实际代码分析：

1. **章节标签匹配** - 高准确率(0.9)，但只匹配"教育经历"、"项目经验"等固定模式
2. **实体名分类** - 中准确率(0.75-0.85)，识别"四川轻化工大学"→EDUCATION
3. **Section继承** - 中等准确率(0.7)，子块继承上方章节类型
4. **Content fallback** - 低准确率(0.5-0.6)，正则关键词匹配

## 真实数据示例

### V1 → V2分块效果

```
输入 (V1 content_list.json 第117-161行):
- type: "text", text: "科研/项目经历", text_level: 2, bbox: [38, 281, 152, 298]
- type: "text", text: "省重点人工智能实验室", text_level: 2, bbox: [15, 318, 193, 332]
- type: "text", text: "学生科研组成员", bbox: [431, 318, 556, 334]
- type: "text", text: "2024/11 - 至今", bbox: [867, 318, 984, 332]

处理后 (DocumentBlock):
block_id: "b_5"
heading: "科研/项目经历"
text: "省重点人工智能实验室\n学生科研组成员\n2024/11 - 至今"
block_type: "PROJECT_EXPERIENCE" (通过_entity_heading分类)
confidence: 0.80
```

## 流程图问题诊断

### ⚠️ 原流程图的错误假设

1. **YOLOv10版面检测** - 实际上代码中没有YOLOv10集成
2. **三层信号融合** - 实际上是简单的多级pass分类
3. **跨页合并** - 函数存在但未完整实现
4. **Fallback策略** - 没有实际的OCR重试机制

### ✅ 真实流程应该是

```
PDF → MinerU解析 → 选择格式(V1/V2/Middle)
       ↓
V2格式 → flatten → parse文本 → 按标题分块 → DocumentBlocks
       ↓
三级分类策略处理
       ↓
输出: List[DocumentBlock]
```

这个流程图才是真实反映了你的Phase1实现！