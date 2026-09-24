# 离线发布记录防覆盖（Mac 回归）

发布记录是离线制品核验和回退所需的不可变依据。审查发现生成工具原先仅禁止写入 wheelhouse，最终 `os.replace` 仍可能覆盖既有 `release.json`，也可能误写模型清单或 Web 制品。

本次将输出限制为新路径：拒绝已有文件/符号链接，拒绝 wheelhouse 和 Web dist 内部路径，拒绝模型清单及 `--previous-release` 路径；写入时先在同目录形成并落盘临时文件，再用仅在目标不存在时成功的硬链接原子发布，最后清理临时文件。即使路径检查后出现另一个写入者，也不能覆盖其发布记录。部署说明要求每次使用独立版本目录并保留上一版。

验证：

- `PYTHONPATH=. .venv/bin/pytest -q tests/business/test_release_manifest.py tests/business/test_verify_offline_release.py`：17 通过。
- `PYTHONPATH=. .venv/bin/pytest -q tests/business`：179 通过、1 个 opt-in 跳过、2 个依赖弃用警告。首次受限沙箱运行中，Doclib 的 Unix/TCP 绑定被系统拒绝；获准本地绑定后同一套测试通过。
- `.venv/bin/ruff check scripts/release_manifest.py tests/business/test_release_manifest.py`：通过。两个文件原本不符合当前 `ruff format --check` 的整文件格式；本次没有为了这一小项改动做无关的批量排版。

范围限制：这是 Mac 上使用模拟镜像信息和临时制品的代码保障，不是麒麟 `linux/amd64` 真镜像、真实权重、物理断网或回退演练的验收。P1 目标机门禁保持未完成。开放系统设计不变：无用户、登录和权限管理。
