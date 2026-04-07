# Bug 日志

## 2026-04-06: Input Router 首轮测试 — 3 个 Bug

### Bug #1: Image 通道 `No module named 'layout.yolo_layout_detector'`

- **文件**: `mvp/entity_extractor/phase1_physical_layer.py` L694
- **根因**: `yolo_detector` 属性中 `from layout.yolo_layout_detector import` 缺少 try/except 双路径导入，standalone 运行时找不到模块
- **修复**: 改为 `try: from .layout... except: from layout...` 模式
- **影响**: Image 通道完全不可用

### Bug #2: JD JSON `argument of type 'NoneType' is not iterable`

- **文件**: `mvp/entity_extractor/input_router.py` `_is_it_position()` L285-286
- **根因**: `record.get("所属行业", "")` — 当 JSON 中字段值为显式 `null` 时返回 `None`（key 存在但 value=None），后续 `kw in None` 报 TypeError
- **修复**: 改为 `record.get("所属行业") or ""`，用 `or` 防御 None
- **影响**: 所有含 null 字段的 JD 记录解析失败

### Bug #3: PDF 通道返回 0 blocks (MinerU 路径不匹配)

- **文件**: `mvp/entity_extractor/input_router.py` `_route_pdf()` L870-874
- **根因**: 代码硬编码路径 `{stem}/auto/content_list_v2.json`，但实际 MinerU 输出为 `简历/auto/简历_content_list_v2.json`（文件夹名 ≠ PDF stem，文件名带前缀）
- **修复**: 新增 `_find_mineru_outputs()` 函数，两步搜索策略：精确匹配 → rglob 通配搜索
- **影响**: PDF 简历虽 PASS 但产出 0 个 block，等于空跑
