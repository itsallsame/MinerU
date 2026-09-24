# 任务完成与解析修订的原子提交

日期：2026-09-24。源码基线：fork `master` 的 `ac0b5b2b`。环境：Mac、Python 3.13.5、临时 SQLite 数据库和本地 Doclib 契约测试；目标麒麟未运行。

## 实现

`DocumentWorkflow.refresh` 原本调用两个独立事务：先 `add_completed_revision`，再 `mark_task_done`。现在调用 `BusinessStore.complete_task_with_revision`，同一写事务中检查业务任务仍为 `submitted`、Doclib 批次 ID 属于该任务且实际档位一致，然后插入或复用修订并设置 `done`。如果事务取得锁时任务已进入终态，返回该终态，不插入新修订。解析修订仍可由独立 `add_completed_revision` 保存其他合法的重新解析结果；完成业务任务的路径不再使用可单独标记 `done` 的方法。没有改变业务库 schema，也没有引入用户/权限层。

## 验证

- 定向业务存储与流程测试：26 通过。新增测试覆盖不属于任务的 parse ID、错误档位、修订来源冲突回滚、竞争终态不插入修订、两个并发刷新只产生一份修订。
- 最终代码状态完整业务测试：246 通过、1 跳过、2 个既有依赖警告，耗时 70.08 秒。
- Ruff lint 与 `git diff --check` 通过。受影响的旧业务文件本身有大量既存的 formatter 差异，未做全文件格式化以避免无关改写。

## 范围

此改动只关闭业务任务完成/修订写入之间的原子性缺口。Doclib 还没有消费者引用或安全取消队列的公共契约，因此不能据此宣称 FE-016、GPU 抢占或麒麟取消验收完成；当前无“已停止底层计算”的 Web/Skill 入口。真实外部进程竞争仍需在取消协议形成后验证。
