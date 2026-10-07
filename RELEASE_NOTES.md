# BigData v10.1.13 — Release Notes

发布日期：2026-10-06（学生离线发行版）

## v10.1.13 变更（相对 v10.1.10）

- **模型选择交给学生**：`llm.model` 从课程 Runtime Policy 中移除（不再有课程默认模型）；
  模型名由学生在 `workspace/.env` 的 **`LLM_MODEL`** 自行配置（必填，校验缺失即报错）。
- `policy/runtime_policy.json`（v1.2）只保留执行资源字段：`request_timeout_seconds` /
  `token_budget_per_execution`（`.env` 写 `LLM_TIMEOUT` / `LLM_TOKEN_BUDGET` 仍被忽略并警告）。
- `result.json` 的 `runtime_policy` 快照新增 `model_source: "student .env (LLM_MODEL)"`，
  同时记录学生选择的模型名（非敏感）。
- `.env.example` / `datac init` / Matrix 生成模板均包含 `LLM_MODEL=` 字段。
- 教师如需按班统一模型，可在 suite 级配置（benchmark suite llm.model）或收回教材策略；
  学生默认自主选型。

## v10.1.13 变更（相对 v10.1.11）

- **Exposure Contract Fix**：
  1. `datac exposure import` 支持 canonical nested 布局（`tasks/AT*/task/task.json` + `task/data/`），legacy flat 兼容；
  2. 匿名视图剥离补全：`expected_failure_mode` / `author_expected` 等金标语义字段在 anonymous view 生成时即被剥离（`hypothesized_failure_mode` 为作者公开假设，保留）；
  3. Assignment READY 门新增 Exposure 语义扫描：分发中任意 `FORBIDDEN_TASK_KEYS` 命中 → `DISTRIBUTION_FAILED`（绝不 READY），`export_reviewer_archives` 仅 READY 可导出；`assignment verify` 重放同步带该门。
- **Submission Resubmission Fix**：
  1. `DUPLICATE_ARCHIVE` 仅在**完全相同 SHA256** 时触发；同名不同 SHA = 新提交内容；
  2. 校验 FAIL 的包不再占用 raw 槽位 → `history/rejected/<stem>__<sha8>.zip`；
  3. 新增 `--supersede`：显式替换 ACTIVE（旧包入 `history/superseded/`），新包仍需完整校验；
  4. `pool/FROZEN` 冻结标记：冻结后 receive/supersede 一律 `PHASE_FROZEN`（补交走 late submission 入口）；
  5. `pool/audit/submission_attempts.jsonl` 每次 receive 追加一行审计（REJECTED/ACTIVE/DUPLICATE_ARCHIVE/REFUSED_SUPERSEDE）。
- batch-validate 语义不变：仍然只读 `pool/raw/*.zip`（每学生每 phase 唯一 active）。

## 概要

BigData v10.1.13 是课程 **Two Submissions, One Exposure** 模型的正式学生发行版。
公共 CLI 由 `codesetarena` 正式迁移为 **`datac`**（`codesetarena` 保留为 deprecated 兼容别名，
学生文档不再出现）。

## 主要变化

- **public CLI migrated to `datac`**：`datac --help` / `--version`（输出 `BigData 10.1.13`）、
  `datac doctor`、`datac init`、`datac run`、`datac exposure import|status|run`、
  `datac submission build|validate`。
- **Two Submissions, One Exposure**：两次正式提交（P1 / P2），中间唯一一次 Exposure。
- **canonical P1/P2 naming**：正式提交包名为 `BigData_P1_{student_id}.zip` /
  `BigData_P2_{student_id}.zip`（固定命名、无时间戳；正式格式 `.zip`，`.tar.gz` 为 legacy 兼容）。
- **unified single-submission validator**：学生 build、学生 validate、TA receive、
  batch 第一遍全部复用同一个 Single-Package Validator（无第二套规则）。
- **anonymous 10-task exposure**：Exposure 包**恰好 10 个匿名 Task**（正式 Contract 数量）；
  导入时强制结构校验与泄漏扫描（author identity / gold / reference / expected 一律拒绝）；
  Exposure 不产生任何学生正式提交。
- **secret-safe submission packaging**：`.env` / `runtime_policy.json` / `*.key` /
  `credentials` / `secret` 等在 Build 端自动排除、Validator 端 ERROR 拒绝（Package Secret Guard）；
  `.env.example` 为连接模板（Build 排除，legacy 包内仅 WARNING）。
- **Docker student runtime**：镜像 `bigdata-student:10.1.13`（多阶段构建，依赖全部烘焙，
  运行时零 `pip install`、零网络拉取），Compose 挂载宿主机 `workspace/`。
- **offline image delivery**：发行包含 `images/bigdata-student-10.1.13.image.tar`
  + `SHA256SUMS` + Windows/Linux 包装脚本。学生只需 Docker Engine + Compose 插件。
  安装离线 ≠ 推理离线：课程 LLM API 调用需要网络。
- **PyYAML** 已加入依赖与镜像（修复 v9 测试环境中 Compose 契约测试缺依赖的问题）。

## 与 9.0.0 的兼容性

- 提交包命名/格式变更（`DataC1004_*` tar.gz → `BigData_*` zip）：旧包仍可被 Validator 读取（legacy）。
- `assignment`（TA 评审分配）管线升级为每评审员恰好 10 个任务（reviews_per_task = tasks_per_reviewer = 10）。

## 范围外（OUT_OF_SCOPE_FOR_V10.1.10）

- 全班 Agent × Task 批量运行（`matrix` 工具保留，但不作为学生生命周期的一部分）。
- 最终学生成绩计算 / grading（Exposure 与运行结果均不评分）。
