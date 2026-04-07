# Layer 0: 数据摄入与解析

## 核心依赖

| 组件 | 作用 | 性能 |
|------|------|------|
| MinerU (magic-pdf) | PDF双通道解析(metadata+OCR) | OmniDocBench 86.2 |
| YOLOv10 ONNX | 版面区域检测(多列/侧边栏) | mAP@0.5=92.1% |
| Qwen3-0.6B-SFT | 并行3子任务信息抽取 | F1=0.964, 1.22s/份 |
| vLLM | 模型推理服务 | ~150 tokens/s |
| BGE-M3 | Embedding语义匹配(Layer2/3用) | dim=1024, 中英多语言 |

## 0A. 简历解析流程

```
PDF/Image/TXT
  ↓ 路由: PDF→MinerU+YOLOv10, TXT→直接加载, DOCX→转PDF
  ↓
双通道融合: metadata文本 + OCR图片区域 → spatial_fusion去重
  ↓
YOLOv10版面检测 → layout_segments[bbox, class, conf]
  class: main_content | sidebar | header | footer
  过滤: conf < 0.5 丢弃
  ↓
层次化重排序:
  1. Inter-segment: top→bottom, left→right (按bbox左上角)
  2. Intra-segment: 同上
  ↓
索引化线性化: "L001: 陈思远\nL002: 男｜1998年3月\n..."
  ↓
Qwen3-0.6B-SFT 并行抽取 (ThreadPoolExecutor × 3):
  Task A: basic_info (姓名/性别/手机/邮箱/求职意向)
  Task B: education + work/internship experience
  Task C: skills + certificates + projects + awards + languages
  ↓
Index-based Pointer: LLM返回行号范围[L015,L025]→从原文截取
  ↓
后处理4阶段 (SmartResume §3.2.3):
  1. Grounded re-extraction: 行号→原文(100%内容保真)
  2. Domain normalization: 日期格式统一/公司名后缀清理
  3. Context-aware dedup: 项目∈工作经历→去重
  4. Source verification: 关键字段(公司名+职位)回查原文,查不到→剪枝
```

## 0B. 边界处理

| 场景 | 策略 |
|------|------|
| 扫描件PDF(无metadata) | 全OCR路径 |
| 多列简历(~30%) | YOLOv10分列→列内排序 |
| 表格型简历 | pdfplumber table解析→转行文本 |
| 乱码/加密PDF | OCR fallback + data_quality标记 |
| 空白/极短简历 | completeness_score=低,不剔除(R-02) |

## 0C. JD解析

```
Excel 10000条 → pandas读取
  ↓
IT岗位过滤 (R-01):
  行业 ∈ {互联网,通信,电子,计算机,软件,信息技术,AI}
  OR 职位名含 {开发,工程师,架构,运维,测试,数据,算法,产品经理...}
  ↓ ~5000条有效JD
规则提取:
  学历: 正则 r"(本科|硕士|博士|大专|不限)"
  经验: 正则 r"(\d+)-?(\d+)?年"
  薪资: 正则 r"(\d+)-(\d+)[kK万]"
LLM提取(仅规则无法处理的字段):
  技能要求 → required_skills[] + preferred_skills[]
  专业要求 → major_requirements[]
  岗位职责 → responsibilities[]
```

## 0D. SFT蒸馏管道

```
阶段1: Gold标注生成
  简历PDF → Layer0A解析 → GPT-4o/Claude标注 → 人工校验
  目标: 500份Gold (SmartResume曲线: 500份→F1≈0.92拐点)
  来源: data/resume/ ~70份PDF + Teacher模型标注

阶段2: 训练
  Gold → (instruction, input, output) triplets
  每份简历3子任务 → 1500条训练样本
  配置 (SmartResume §4.1.4):
    model: Qwen3-0.6B, optimizer: AdamW
    lr: 5e-6, scheduler: cosine, batch: 2, grad_accum: 2
    epochs: 3, max_seq: 4096, method: full_parameter
    GPU: ~8GB (单卡RTX3060即可)
```

## 0E. JSON容错解析

```python
def parse_json(raw: str) -> dict:
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try: return json.loads(raw[start:end+1])
        except: pass
    # 修复常见错误: 尾逗号/单引号/未转义换行
    cleaned = fix_common_errors(raw[start:end+1])
    try: return json.loads(cleaned)
    except: return {"_parse_error": True, "_raw": raw}
```
