# 四类样本评估输入去重与页码校验

日期：2026-09-24。范围：`scripts/evaluate_business_samples.py` 的只读验收输入；不修改业务平台或模型。

## 问题与修正

旧版只校验 case ID 唯一。同一原文件或同一业务文档可被列为多个 case，重复计入字段召回、候选精确率和证据命中率。字段 `page_no` 使用 Python `isinstance(value, int)`，会把 JSON `true` 当成第 1 页。源路径先 `resolve()` 再测 `is_symlink()`，使样本目录内的符号链接原文未被拒绝。

现在每个 case 必须对应独立原文字节 SHA-256 和独立业务文档 ID；字段页码必须是真正的正整数；源路径的每个相对路径组件都不能是符号链接。原文、标注和值仍不进入 Git 或报告。

## 验证边界

定向测试 `tests/business/test_sample_evaluation.py`：10 passed，覆盖重复文档、重复原文、布尔页码和目录内符号链接。完整 `tests/business`：345 passed、2 skipped、2 个第三方依赖警告；Ruff lint 和差异检查通过。测试使用合成样本，不代表四类真实准确率；真实脱敏原文、人工标注、麒麟 x86_64/NVIDIA 和现场结果仍缺失，P6 不标完成。
