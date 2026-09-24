# DocVortex MHTML 新后缀回归（2026-09-24）

## 发现与处理

Mac 运行官方 `tests/unittest` 时，`FileSuffix` 契约测试首先失败：安装的 DocVortex 已把 `mhtml` 加入共享后缀，但 MinerU `doc_analyze` 只显式分流 HTML，剩余格式全部强转为 Office。因而 MHTML 虽可被来源识别和元数据层接受，正文会走不支持的 Office 分支。此次将 MHTML 送入 DocVortex **公开** `analyze(..., file_suffix="mhtml")`，把原生 model-list 接回 MinerU 的严格 ModelJson/MiddleJson 和产品 Flash 元数据；`.mhtml`、`.mht` 统一归一为 `mhtml`，进入本地 Flash、上传和 Doclib 格式能力集合。没有改模型、用户权限或远程服务配置。

## 验证

- 新增 MIME 归档小样本，覆盖同步/异步 Analyze、标题与正文、实际 Flash/txt 元数据以及两个文件后缀的公开 `parse` 路径；针对性测试 18 项通过。
- 完整官方单元套件：`2805 passed, 4 skipped, 2 warnings`，Mac Python 3.13.5，耗时 184.11 秒。原本的共享后缀契约失败已消失。
- 完整业务套件：`241 passed, 1 skipped, 2 warnings`，Mac，耗时 68.50 秒；随后在开放业务 API 能力契约中补上 `.mhtml`/`.mht` 两种格式的显式断言，单独复测该用例。
- Ruff lint、六个新增/改动的核心格式文件 format check 与 `git diff --check` 通过。业务测试文件已有未格式化段落，此次只增加两条断言，未批量改写其余测试。
- 首次非提权全量测试在 CLI `server start` 测试因默认 `~/.mineru/doclib.start.lock` 不可写而失败；单测复现。按本项目既有本机权限方式重跑后全量通过。此项是测试运行权限，不是 MHTML 回归。

## 边界

样本为合成静态 MHTML，仅证明解析路由与平台入口；未证明真实浏览器保存的复杂归档、内嵌资源完整性、麒麟/NVIDIA、四类业务脱敏样本或离线部署。前端源文件预览仍不把 MHTML 冒充普通 HTML；可使用解析结果及原件下载。
