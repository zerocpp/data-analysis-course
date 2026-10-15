# Assignment Operations（管理端操作手册）

Peer-Review Assignment Pipeline：从 Canonical Repository 到匿名 Reviewer 分发。
本页面向课程管理 / 助教；学生侧说明见 `学生手册_Phase2_Review_Current.md`。

## 架构与身份分离

四类关系必须严格分开（结构性强制，不依赖"不泄露"的口头约定）：

| 身份层 | 对象 | 存放位置 |
|---|---|---|
| Submission Identity | student_id / submission_id | `registry/submission_registry.jsonl`（PRIVATE） |
| Canonical Identity | task_id(T######) / agent_id(A######) | `tasks/`、`agents/`（对象内无 student_id） |
| Anonymous Identity | anonymous_task_id(AT###) / anonymous_agent_id(AA###) | `distributions/tasks/`、`distributions/agents/`（opaque，可逆追溯在私有映射） |
| Reviewer Identity | reviewer_student_id ↔ reviewer_package_id(R######) | `registry/assignments.jsonl` + `assignments/<AR>/private_mapping.json`（PRIVATE） |

规则：

- 学生最终只看到 `assignment.json` 中的 `anonymous_task_id`（AT###）与任务内容；
- 任何学生可见文件不得出现 student_id / source_task_id / canonical task_id /
  original archive 文件名 / gold / reference / 私有 evaluator metadata；
- 完整授课映射（reviewer ↔ canonical task ↔ owner ↔ AT）只存私有 Registry；
- 匿名 ID 是 opaque（AT### 按 ingest 顺序分配但 canonical 与 AT 无直推关系，
  禁止 T000001→AT001 式可推断映射）；同一 canonical Task 在同一课程内恒为
  同一 anonymous_task_id（`anonymous_mapping.jsonl` 为唯一事实来源）。

## 流水线总览

```
Submission tar.gz → single validation → batch validation → ingest
        → Canonical Repository (tasks/agents/registry)
        → assignment build（输入快照 → CATEGORY_AWARE_SWAP → 独立 Verifier）
        → assignment distribute（私有映射 → 匿名分发包 → 分布校验 → 泄露扫描）
        → READY → 按私有映射导出 per-reviewer archive
```

Assignment 只读取 ingest 完成的 canonical accepted tasks；Single/Batch FAIL 的
submission 不存在于 registry，不可能进入 assignment。Ingest 与 Assignment
严格分阶段：Ingest 回答"有哪些合法 Task/Agent"，Assignment 回答"分给谁"。

## 操作步骤

### Step 1 — 确认 Ingest 完成

```bash
codesetarena submission --mode ingest --input <batch目录> --output <repo>
# status=PASS 且 ingested=N 之后再进入 assignment
```

### Step 2 — 执行 Assignment Build（输入校验 → 求解 → Verifier）

```bash
codesetarena assignment --mode build --repository <repo> --phase phase1 [--seed <S>]
```

- 输入自动构建：reviewer = registry 中该 phase 的 accepted owners；
  task = canonical `tasks/T*`；category = canonical `metadata.json.failure_mode`
  （14 类 FM 词汇，缺失/非法 → `ASSIGN_INPUT_*` 失败，solver 不启动，绝不
  用 LLM 重猜或默认填充）；
- solver = `CATEGORY_AWARE_SWAP`（policy/assignment_policy.json，r=5），
  无静默 fallback；
- Verifier 从零重算 shape / binary / load / coverage / self / duplicate；
  FAIL → `VERIFICATION_FAILED`，不产生任何 mapping / distribution；
- 产物：`assignments/<AR######>/{run.json, input_snapshot.json,
  category_vector.json, assignment.npy, assignment.csv(PRIVATE), verifier.json,
  audit.json}`；`input_snapshot_hash` + seed + policy_version 保证可复现。

推荐显式固定 seed（例如按批次日期 `20261005`），并记录在 run.json。

### Step 3 — 查看 Verifier / 审计

```bash
codesetarena assignment --mode verify --repository <repo> --assignment-run AR000001
cat <repo>/assignments/AR000001/{run.json,audit.json,verifier.json}
```

`diversity_score` / `exposure_mae` 是审计指标（管理查看用），不是 Acceptance
Gate；Acceptance Gate 只有 Hard Constraints。

### Step 4 — 执行匿名分发

```bash
codesetarena assignment --mode distribute --repository <repo> --assignment-run AR000001
```

内部顺序：私有映射（`registry/assignments.jsonl` append，原子）→
`distributions/<AR>/reviewers/<R######>/`（`assignment.json` + `tasks/AT*/`）→
分布校验（§38 全项）→ 身份泄露扫描（student/source/canonical/archive/
registry 全 token）+ 私有内容扫描（gold/reference/evaluator 等）。任一失败 →
`DISTRIBUTION_FAILED`，不 READY。

### Step 5 — Leakage / 分发复核

```bash
codesetarena assignment --mode verify --repository <repo> --assignment-run AR000001
# 输出 READY 表示：distribution verified + leaks = 0
cat <repo>/assignments/AR000001/{distribution_verification.json,leakage_scan.json}
```

### Step 6 — 导出 Reviewer Archives

```python
# 管理脚本（或按需调用 API）：
from codesetarena.assignment import distribution as D
D.export_reviewer_archives(Path("<repo>"), "AR000001")   # -> distributions/<AR>/exports/review_package_<R######>.tar.gz
```

- 每个学生拿到**独立** `review_package_<R######>.tar.gz`（`assignment.json` +
  `tasks/`），不打包整个 distributions/；
- `private_mapping.json` 决定 `R###### ↔ student_id`（管理端私有）；
- 每个 archive 生成后执行 `validate_reviewer_archive()`（结构 / 数量 / 匿名 ID /
  无路径逃逸 / 无身份 token）。
- 若给学生发完整包（不逐个导出），可改用
  `codesetarena assignment --mode report` 后的 `distributions/<AR>/reviewers/`，
  但**必须**先通过泄露扫描；路径只用 R 编号，不得出现 student_id。

### 一键编排

```bash
codesetarena assignment --mode prepare --repository <repo> --phase phase1 --seed <S>
# = build（含 verifier） → distribute（含分布校验 + 泄露扫描）
# 底层函数（build_assignment_run / distribute_assignment_run）仍然独立可调。
```

## Run 状态机

`INPUT_PREPARED → SOLVED → VERIFIED → MAPPED → DISTRIBUTION_BUILT →
DISTRIBUTION_VERIFIED → READY`；失败态：`INPUT_INVALID / SOLVER_FAILED /
VERIFICATION_FAILED / MAPPING_FAILED / DISTRIBUTION_FAILED`。

只有 `READY` 才允许正式发放；`VERIFICATION_FAILED` 与 `DISTRIBUTION_FAILED`
的 run 仍保留全部 artifact 供审计。

## 幂等与版本

- 同 `(input_snapshot_hash, method, seed, policy_version)` 重复执行直接复用
  已有 run（`RUN_EXISTS`），绝不静默覆盖；
- 每个 run 记录 `method_version`（solver）、`policy_version`、
  `category_schema_version`（fm14.v2.0）、`distribution_schema_version`。

## 结构化错误码（示例）

`ASSIGN_INPUT_*`（输入）、`ASSIGN_SOLVER_FAILED`、`ASSIGN_VERIFY_*`（Verifier）、
`ASSIGN_MAPPING_FAILED`、`DIST_TASK_COUNT / DIST_SELF_LEAK / DIST_IDENTITY_LEAK /
DIST_PRIVATE_CONTENT_LEAK / DIST_UNKNOWN_TASK / DIST_MISMATCH_MATRIX /
DIST_ARCHIVE_INVALID`（分发）。CLI 退出码：0 = 成功/READY，1 = assignment 失败，
2 = 配置/内部错误。

## 教学规则说明（Category / Agent 可见性）

- Task Category（`hypothesized_failure_mode`，14 类 FM）是 assignment 的输入
  metadata；当前分发视图沿 ingest 的匿名视图契约保留该字段（学生可见的
  task.json 中含 FM 标注）。若课程要求 blind（reviewer 不可见 FM），需修改
  `submission.anonymization.TASK_VIEW_STRIP_FIELDS` 并重新 ingest——本轮不改
  教学规则，只记录该选项。
- Phase2 流程为「Reviewer 用自己的 agent v1 运行收到的匿名任务」，因此
  assignment 只分发 anonymous **tasks**；Task 作者的 agent 不随任务发放。
  若课程未来要求「评审匿名 agent」，需单独建立 Agent Assignment。

## Phase F —— Release Gate（Phase1 Final Acceptance + Phase2 Release Candidate）

Phase F 是正式发布门（不是再做一次 dry-run），分三步：**验证 RC ≠ 已发放**。

### Step F1 — Phase1 Final Acceptance（Gate A）

对助教当前持有的原始 200 个 Phase1 `*.tar.gz` **从字节开始重新独立校验**
（不复用历史 Build/Batch/Dry-Run PASS）：

```bash
.venv/bin/python scripts/phase2_release_candidate.py \
    --workspace workspace/cohort_dryrun_200 --assignment-run AR000001 \
    --expect-assignment-sha256 <冻结的 assignment.npy SHA256>
```

- 每包重新执行 `validate_submission_archive`（envelope → safe extract →
  唯一 Single-Package Validator），再对全部 PASS Candidate 重新执行
  `validate_cross_package`；原件前后 SHA256 必须零变化；
- 与 Canonical Repository 逐行对账（registry ↔ archive ↔ canonical Task/Agent
  ↔ anonymous mapping）；任何漂移即 `PHASE1_CANONICAL_DRIFT`，**绝不自动
  re-ingest 修复**；
- 通过后冻结：`phase1_archive_inventory.jsonl`、
  `phase1_final_acceptance_manifest.jsonl`（后续一切只引用这份记录）、
  `phase1_submission_set_sha256`、`phase1_final_validation_report.xlsx`。
  终态 `PHASE1_FINAL_VALIDATED`。

### Step F2 — Phase2 Release Candidate（Gate B）

只有 Gate A 200/200 后，才基于**已冻结**的 Assignment Run 构建 RC：

- 不重跑 solver、不重建 Assignment、不重建匿名映射；重新执行独立
  Verifier + Input Snapshot 与 canonical repo 的一致性（漂移即
  `ASSIGNMENT_INPUT_DRIFT` / `ASSIGNMENT_ARTIFACT_DRIFT`）；
- RC 复用**正式** builder（`assignment.distribution.build_reviewer_package` /
  `build_reviewer_archive`，确定性字节：固定 mtime/uid/gid、空 uname/gname、
  归一化 mode、gzip 头固定），但只写入隔离区
  `release_candidates/<AR>/{packages,expanded,manifests,reports}/`，
  **绝不写正式 `distributions/<AR>/reviewers/`**；
- 200 包逐一校验（结构 / assignment.json schema / 恰好 5 匿名任务 /
  禁止文件 `.env`、`agent.py`、`reference.py` 等 / tar header 泄露面）+
  No-Self（Registry 权威 owner 逐包复查）+ Exposure 5/5 +
  **反向重建矩阵 bit-exact == assignment.npy**（`difference_count = 0`）+
  身份 / 私有内容 / Secret / 本地路径四类泄露 = 0；
- 抽样二次重建比较 SHA256（verified bytes == future released bytes）；
- 冻结 `phase2_release_candidate_manifest.jsonl`（public/private 分离，
  private 才含 `R###### ↔ student_id`）与整批
  `phase2_release_candidate_set_sha256`，写 `release_lineage.json`
  （Phase1 set → canonical snapshot → input snapshot → matrix → anonymous
  mapping → RC set）。终态 `RELEASE_CANDIDATE_VERIFIED`。

### Step F3 — Explicit Release（本轮禁止，未来唯一发放路径）

未来真正发放时：**不重新生成 Assignment / 匿名映射 / Package**。只允许读取
`RELEASE_CANDIDATE_VERIFIED` 且 SHA256 与冻结 manifest 完全一致的同一批
200 个 RC `tar.gz`，执行显式发放动作（人工/管理端），之后状态才进入
`RELEASED`。验证对象与发放对象必须是同一批字节。

产物位置（Phase F）：`workspace/<cohort>/final_acceptance/`（Gate A）、
`workspace/<cohort>/release_candidates/<AR>/`（Gate B 隔离区）。机器可读
JSON/JSONL 为权威，`phase2_release_candidate_report.xlsx` 仅为管理端视图。