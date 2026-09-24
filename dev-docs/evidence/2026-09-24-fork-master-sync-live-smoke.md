# Fork master 同步与本机真实链路复核（2026-09-24）

## 交付状态

本地 `master` 原领先 `origin/master` 110 个已提交版本。推送前读取 GitHub fork 的 `refs/heads/master`，确认仍为 `48a92548263a919f49138d856d2581698536c474`，且该提交是本地 HEAD 的祖先；`git diff --check origin/master..HEAD` 与 `git push --dry-run origin master` 均通过。随后执行 `git push origin master`，再次用 `git ls-remote origin refs/heads/master` 核对远端为 `a24b782df6958c92f792ef92000884d857acf820`，与本地 `master` 一致；工作树干净。未建新分支、未改上游仓库。

## 本机真实链路

- macOS、回环地址、当前 fork 提交 `a24b782d`；前端 `pnpm test && pnpm build`：11 项通过、构建 8 个离线资源。
- `MINERU_RUN_LIVE_BROWSER=1 .venv/bin/python -m pytest -q tests/business/test_live_web_smoke.py`：1 项通过（28.59 秒）。临时启动真实业务 API 和 Doclib，不拦截业务 API；公共 `demo/pdfs/demo1.pdf` 的 SHA-256 为 `f3b3be345bf2df8979f2491ca9466e078e4fd1d6a216611faa8566e4c44d474b`。临时生成 DOCX/PPTX/XLSX，经 Web 上传、Flash 解析、历史读取、复核确认及 JSON 导出；自有 Skill 从同一 API 读取已确认成果，并对另一次 PDF 上传执行解析、读取与提取。测试结束关闭临时服务并清理临时数据。
- `pnpm test:browser`：22 项离线 Chromium 回归通过。首次在普通沙箱中启动时，本机端口绑定被拒，测试未运行；授权回环端口后原命令通过。这是执行环境权限，不是应用断线结果。

## 尚未证明

公共 PDF 和临时 Office 文件不是四类人工标注业务样本；本次只覆盖 Mac 的 Flash/CPU 路径，不覆盖 OCR、Basic/Standard/Advanced、模型权重、vLLM、麒麟 x86_64/NVIDIA、物理断网或目标机回滚。不能据此将 QA-007–QA-035 或 P1 目标机项目标为完成。GitHub 同步证明代码已到用户 fork，不证明可运行的 Linux amd64 镜像、wheelhouse 和模型已准备。
