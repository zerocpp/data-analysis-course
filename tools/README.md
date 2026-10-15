# tools/ —— 教师工具完整拷贝（v10.1.19）

> 拷贝日期：2026-10-15｜来源：仓库根 ReleaseWork 工作树（`VERSION`=10.1.19）
> 全部内容为源文件的**逐字节完整拷贝**（已用 `diff -rq`/`cmp` 校验，651 个文件）。
> 本目录仅作归档/查阅/移交用；**运行仍以仓库原始路径为准**（教师工具依赖
> `src/codesetarena` 完整包与 `.venv`，见文末「依赖说明」）。
>
> **2026-10-15 新增：`BigDataMail/`（邮箱收取 Stage 1 学生提交包，独立可运行工程）。**
> **使用前务必先阅读 `BigDataMail/README.md`**，详见下文第 5 节。

## 1. 教师工具源码包（`src/codesetarena/`）

| 拷贝路径 | 原始路径 | 工具 | 调用入口 |
|---|---|---|---|
| `src/codesetarena/eval/` | 同名 | 统一评测 Runner（单次 + 批量串行 + 汇总） | `datac eval run / eval batch` |
| `src/codesetarena/task_quality/` | 同名 | Task Quality Gate（Blind Independent Reference 质量门禁） | `datac task-quality-gate` |
| `src/codesetarena/phase1/` | 同名 | Phase-1 教师流水线（export→canonical→batch validate→registry→FM distribution→frozen pool→FM 分层分配） | `datac assignment` 及配套脚本 |
| `src/codesetarena/assignment/` | 同名 | 审稿分配生产管道（submission/canonical/anonymous/reviewer 四身份严格分离） | `datac assignment`（`assignment.cli`） |
| `src/codesetarena/release/` | 同名 | Phase F 发布门（Gate A 重验 200 SHA 冻结包 / Gate B 发布候选构建），verify-only | `scripts/phase2_release_candidate.py` 等调用 |
| `src/codesetarena/submission/` | 同名 | 提交契约链（服务器端 receive/batch-validate/ingest；与学生端共用唯一 Validator） | `datac submission-receive / submission-batch-validate / submission-ingest` |
| `src/codesetarena/demo/` | 同名 | Runtime 资源限制教师演示（4 案例，独立于正式流程） | `scripts/demo_runtime_limits.sh` |
| `src/codesetarena/main/matrix.py` | 同名 | Benchmark Matrix（K Model × N Task × M Agent，复用 run 核心） | `datac matrix --suite ./benchmark/suite` |

## 2. 教师操作 / 构建脚本（`scripts/`，完整目录）

`add_late_submission.py`、`build_assignment.py`、`build_experiment_truth_tables.py`、
`build_fm_distribution.py`、`build_phase1_pool.py`、`build_phase2_package.py`、
`build_synthetic_assignment_repo.py`、`cohort_dryrun_200.py`（200 人队列 dry-run）、
`mock_repair_submissions.py`、`phase2_release_candidate.py`、`run_v10_1_16_e2e.py`、
`run_release_smokes.py`、`compile_test_fixtures.py`、`run_dac_test_gate.py`、
`run_integration_cases.py`、`complete_manual_templates.py`、
`build_student_docker_release.sh`、`build_bigdata_release.sh`（学生发行包构建）、
`demo_runtime_limits.sh/.bat`、`docker/`（`read_version.py`、`assemble_release_no_daemon.sh`）。

## 3. 测试实验室（`BigData-TestLab/`，含 fixtures 与历史 results）

- `build_fixtures.py`：确定性 Task Package fixture 生成（/tmp 工作，回同步，规避 DrvFs 慢）
- `run_regression.py`：在学生 Docker 镜像内跑回归（`--image bigdata-student:10.1.19 --label <tag>`）
- `fixtures/`、`results/`：冻结 fixture 与历史回归结果

## 4. 配置示例与教师文档

- `examples/`：`eval_configs/{batch,deepseek,qwen}.json`（批量评测配置样例）、`deterministic_workspace/`
- `docs/`：`Eval_Runner.md`（评测操作手册）、`Assignment_Operations.md`（分配操作）、
  `Phase1_Minimum_Product_Contract.md`、`Phase1_Product_Gap_Review.md`、
  `Phase1_Student_CLI_Proposal.md`、`Phase1_Student_Journey_Product_Gaps.md`、
  `RUNTIME_LIMITS_DEMO.md`、`分配审稿建模.md`、`脚本.md`（脚本说明）
- `assignment.py`：根目录审稿分配独立演示脚本（10 学生 × 2 题，seed 可复现；非生产管道）

## 5. 邮箱收取工具（`BigDataMail/`，2026-10-15 新增，独立可运行）

> **使用前必须先阅读 `BigDataMail/README.md`**：运行命令、参数与安全边界均以该 README 为准。

- 独立 src-layout Python 工程（`mailgain` 包，`uv` 管理），**不依赖**仓库根
  `src/codesetarena` 与 `.venv`，可单独运行（与本目录其余归档工具不同）。
- 功能：教师/助教侧从网易 163 邮箱经 IMAP SSL 收取学生 Stage 1 题目包：
  - 唯一合法附件名 `bigdata_p1_{学号}.tar.gz`（学号默认 11 位数字，可用
    `--student-id-core-regex` 覆盖），大小写敏感 `fullmatch` 白名单，旧格式
    `{学号}-student-stage1-problems.tar.gz` 一律拒绝；
  - 学号只从邮件主题提取，且必须与附件名中学号完全一致；
  - 默认 dry-run，仅显式 `--download` 才下载；两阶段 UID FETCH，只取已校验
    附件的 MIME part，绝不整封下载（禁 `RFC822`）；
  - 重复提交默认 `preserve-review`，另存 `duplicates/`，绝不覆盖首次有效提交；
  - 每次运行生成 `collect-report.json` / `collect-report.csv`。
- 入口：`uv run python -m mailgain.cli --help`（子命令 `teacher stage1 collect-email`）；
  真实邮箱收取脚本：`scripts/collect-stage1-163.sh`（授权码仅走环境变量，双重确认）。
- 测试：`uv run pytest`（104 项全部通过）；lint：`uv run ruff check .`。

## 依赖说明（为何本目录不能独立运行）

- 教师工具以 `codesetarena` 包内相对导入复用生产内核
  （如 `eval/`→`main.runner.run_project`、`task_quality/`→`main.runner._answers_equal`、
  `submission/`→共享 `validate_single_package`），因此**归档副本不可直接 import 运行**；
  运行方式：在仓库根 `source ./csa` 后使用 `datac …`，或
  `.venv/bin/python -m pytest tests/test_eval_runner.py -q` 验证。
- `datac matrix` 依赖仓库根 `benchmark/suite/`（矩阵输入数据，未随拷贝）。
- DAC 测试脚本（`scripts/run_dac_test_gate.py` 等）依赖外部冻结语料
  `CODESETARENA_FIXTURE_ROOT`。
