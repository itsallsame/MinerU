# 麒麟离线导入后的发布制品复核

状态：核验工具与模拟测试就绪；**没有麒麟 amd64 镜像、目标 NVIDIA 机器、真实模型目录或现场报告**。不能把模拟通过写成生产验收。

`scripts/verify_offline_release.py` 从当前选定的 `release.json` 重建发布记录并逐字段比较：导入镜像的不可变 ID、`linux/amd64` 架构、源码与基础镜像标签、基础层链，wheelhouse 全文件集合／哈希、Web 静态资源／manifest 哈希、模型清单哈希和前一发布清单哈希。然后逐文件读取实际模型目录，对模型清单核对文件集合与 SHA-256。可选 `--source-tree` 检查源码提交和干净工作树；可选 `--output` 将不含权重和业务正文的核验摘要写到独立目录。任何不一致返回非零，不写成功报告。

验证：`.venv/bin/python -m pytest tests/business/test_verify_offline_release.py -q --no-cov` 为 8/8；`.venv/bin/python -m pytest tests/business -q --no-cov` 为 130/130（2 个现有依赖弃用警告）。案例覆盖正确导入、模型／wheelhouse／Web 变化、错误镜像 ID／架构、前一发布清单缺失或变化、失败不写报告及报告路径隔离。新文件 Ruff check 与 format check 通过。真实目标环境仍待验收。

边界：该工具证明“导入实物与选定发布清单一致”，不证明清单最初可信、镜像内运行时可启动、NVIDIA 驱动／CUDA／vLLM 兼容、模型输出质量、断网运行或备份回退。开放业务 API 的内网可达范围也需独立验收；本工具不加入用户或权限模型。
