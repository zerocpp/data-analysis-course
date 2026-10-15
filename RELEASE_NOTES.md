# BigData v10.1.19 — Release Notes

发布日期：2026-10-10（学生离线发行版）

## v10.1.19 变更（相对 v10.1.18）—— Input Contract Hardening + Regression Release

五类输入契约加固（基于 BigData-TestLab 真实 Artifact Fixture TDD 驱动）：

1. **文件名称**：Tasks 内层包严格校验恰好 task.json/data.csv/reference.py
2. **JSON 结构**：task.json 必须为 JSON object 且含 description/task_statement/answer；禁止管理字段
3. **CSV 编码**：统一 UTF-8 读取；GBK/非 UTF-8 编码返回 INVALID_CSV_ENCODING
4. **数据规模**：CSV 大小 ≤1MB / 行数 <10000 / 列数 ≤100（与 workspace 校验共用同一 policy 值）
5. **文件安全**：内层 tar 的路径穿越/绝对路径/symlink 逃逸均结构化拒绝

配套新增 BigData-TestLab 33 个真实 Artifact Fixture（v10.1.18 RED 5/33 → v10.1.19 预期 33/33）。

# BigData v10.1.18 — Release Notes

发布日期：2026-10-10（学生离线发行版）

## v10.1.18 变更（相对 v10.1.17）—— hypothesized_failure_mode Contract Fix

- **hypothesized_failure_mode 彻底移出 task.json**：学生 task.json 固定为
  `{description, task_statement, answer}`；出现该字段即 FAIL
  （`TASK_JSON_FORBIDDEN_HYPOTHESIZED_FAILURE_MODE`）。
- **FM 统一由 metadata.json 管理**：P1/P2 metadata 必含
  `hypothesized_failure_mode`（缺失 → `METADATA_MISSING_HYPOTHESIZED_FAILURE_MODE`）；
  P1 Build 外层 metadata.json 携带 FM；`Tasks_*.tar.gz` 内层不含。
- **Failure Mode Key 废除数字编号**：唯一合法集合为无数字 canonical 14 键
  （常量 `constants.CANONICAL_FAILURE_MODES` 唯一定义）。数字形态在校验层
  一律 `INVALID_FAILURE_MODE_KEY` 拒绝；`normalize_failure_mode` 仅保留给
  历史数据迁移。
- **匿名链路脱敏**：匿名视图生成剥离 FM；Exposure 导入对含 FM 的包直接拒绝；
  助教端 canonical 继续私有保留，供"预设 FM vs 实际 FM"对照分析。
- 单一事实来源：`metadata.json["hypothesized_failure_mode"]`；禁止双写。

# BigData v10.1.17 — Release Notes

发布日期：2026-10-10（学生离线发行版）

## v10.1.17 变更（相对 v10.1.16）—— 报告命名/真实模板 + review 模板纪律 + 移除 demo 匿名题

- **Phase 2 实验报告统一命名 `实验报告.docx`**：Workspace 校验、P2 Build、
  Built Package Validation、助教端接收全部只认 `实验报告.docx`
  （旧名 `实验报告模板.docx` 不再是合法提交名）。
- **真实课程模板随包发放**：`datac init` / 发行包 workspace 直接以仓库
  `docs/实验报告.docx`（33KB 课程 Word 模板）作为 `phase2/实验报告.docx` 种子
  （找不到该文件时回退生成最小 docx）。
- **review-comments.json 提供模板条目**：init 播种的文件内含一条带说明的
  示例（`ATxxx（示例条目，请整条替换）`，score=0），强调——
  **每一道分配给你的题各写一条**（key = 匿名题号，与
  `exposure/assignment.json.assigned_tasks` 完全一致；score 1~5 整数；
  comments 非空）。未替换示例条目时校验会 FAIL（`INVALID_REVIEW_SCORE` /
  `UNKNOWN_REVIEW_TASK`），逼迫逐题补全。
- **发行包移除 demo 匿名题**：`workspace/exposure/` 出厂仅含空的
  `assignment.json` 占位，不再预置 AT001–AT010（v10.1.16 后期已移除播种，
  v10.1.17 延续并写入手册）；学生收到正式分配包后
  `datac exposure import` 导入。
- 其余协议（两阶段 Workspace、bigdata_p1/p2 tar.gz、ingest Built 识别、
  反向重建等）与 v10.1.16 完全一致。

# BigData v10.1.16 — Release Notes

发布日期：2026-10-09（学生离线发行版）

## v10.1.16 变更（相对 v10.1.15）—— Workspace 冻结 + 两阶段 Build Contract

改造 A：**正式冻结 Build 之前的学生 Workspace 协议**

- 统一布局：`workspace/{.env, .env.example, README.md, phase1/, phase2/,
  exposure/, runs/{phase1,phase2,exposure}, store/{packages,builds}}`；
  `datac init` 直接播种整套冻结结构；
- `phase1/` = Task v1 + Agent v1（task.json / data.csv / reference.py /
  agent.py / metadata.json）；`phase2/` = Task v2 + Agent v2 +
  review-comments.json + 实验报告模板.docx + metadata.json；
- `metadata.json` 是每阶段唯一管理文件：`{workspace, student_id, task_id,
  agent_id(, review-comments_id)}`；P1/P2 的 task_id、agent_id 强制连续
  （同一对象第二阶段版本；禁止 TASK_V1/TASK_V2 式命名）；
- 学生 `task.json` 只管理内容四字段 `{description, task_statement,
  hypothesized_failure_mode, answer}`，不再携带 task_id / student_id /
  agent_id；Validator 会**重新运行 reference.py** 并核对
  `answer == 实际执行结果`；
- 新命令 `datac reference run --phase phase1|phase2`：按冻结路径自动读取
  `<phase>/reference.py + <phase>/data.csv`，控制台**忠实打印完整返回对象**
  （契约包装 `{"answer": ...}`，多字段结构原样保留，绝不只打印一个数字）；
- Runtime 升级：`datac run --workspace . --phase P1|P2` —— agent 与 task
  允许来自不同目录（split paths），证据统一落 `runs/<phase>/`；
- Exposure 重构：匿名 Task 落 `exposure/ATxxx/{task.json,data.csv,reference.py}`
  （reference 随匿名 Task 分发）；assignment.json 冻结 schema
  `{student_id, agent_id, assigned_tasks[恰好10]}`；导入先解压->验证->确认
  目标不存在->原子移动，`exposure/ATxxx` 已存在直接 FAIL（**绝不静默覆盖**），
  写路径结构上不可能落到 phase1/phase2；`datac exposure run` **永远使用
  `phase1/agent.py`（Agent v1）**，即使 phase2/agent.py 已存在；
  证据统一落 `runs/exposure/ATxxx/`。

改造 B：**正式冻结两阶段 Build Contract**

- 学生每阶段只产生一个正式总包，统一 **.tar.gz**（不再输出 ZIP）：
  - `bigdata_p1_{学号}.tar.gz`：解压后 `bigdata_p1_{学号}/` 一级**严格 3 个
    文件**：`Tasks_{学号}.tar.gz` + `Agent_{学号}.tar.gz` + `metadata.json`；
  - `bigdata_p2_{学号}.tar.gz`：一级**严格 5 个文件**（+ `review-comments.json`
    + `实验报告模板.docx`）；版本由 `metadata.workspace=phase2` 表达，文件名
    不加 v2；
  - `Tasks_{学号}.tar.gz` 内恰好 `task.json/data.csv/reference.py`（无
    task_id）；`Agent_{学号}.tar.gz` 内恰好 `agent.py`；
- 新命令 `datac build --phase P1|P2 --workspace . --student-id <学号>`：
  P1 只读 phase1/，P2 只读 phase2/；`.env/README/assignment.json/runs/
  exposure/store/build_manifest` 结构性不可能进入包；
  `build_manifest.json` 由 Build 自动生成到 `store/builds/`，不再要求学生维护；
- Validator 分层（§28）：**Workspace Validation**（`main.workspace_validation`）
  与 **Built Package Validation**（`submission.built_package`）明确分开；
  `datac submission validate --input bigdata_pN_*.tar.gz` 自动走 Built 校验
  （一级清单/子包结构/metadata schema/answer 重算/报告 docx）；
- 助教端新增 `datac receive-built --archive ... --out-root ...`（§24）：
  安全解压 -> Built Validation -> 落入
  `submissions/phase1/<学号>/{Tasks,Agent,metadata.json}`（P2 多 2 件）；
  原始包永久留存 `received/`；同 sha 幂等 SKIP，同名不同 sha FAIL；
  既有 raw/canonical 服务器池不受影响。

# BigData v10.1.15 — Release Notes

发布日期：2026-10-08（学生离线发行版）

## v10.1.15 变更（相对 v10.1.14）—— 两阶段模板预解压 + 随包还原压缩包

- **workspace 预置两套已解压模板，解压完发行包即可直接开工**：
  - `workspace/phase1/` = Phase 1 模板（`BigData-Phase1-Sample` 内部结构）；
  - `workspace/phase2/` = Phase 2 模板（P1 六件套 + assignment.json + 10 匿名 Task +
    review-comments.json + 实验报告模板.docx；demo 分配 R000001）；
  - 学生**零拷贝、零创建、无需 `datac init`**。
- **随包提供两套原始压缩包**：`workspace/_restore/BigData-Phase1-Sample.zip` 与
  `BigData-Phase2-R000001.zip`（内部前缀 `phase1/`、`phase2/`）。学生改坏模板时，
  把 zip 解压到 `workspace/` 即可覆盖还原（`unzip -o workspace/_restore/*.zip -d workspace/`）。
- 容器 entrypoint 播种保护升级：workspace 内存在 `phase1/` / `phase2/` 目录时，
  根级模板播种不再触发（绝不覆盖预置模板）。
- 官方命令路径统一为 `--workspace /workspace/phase1` 与 `--workspace /workspace/phase2`
  （阶段仍自动识别，无需 `--phase`）。
- 教师端正式分配发放工具不变：`scripts/build_phase2_package.py --assignment-dir ...`
  产出的 `BigData_Phase2_xxx.zip` 学生解压到发行包根目录即自动落位覆盖 `workspace/phase2/`。

## v10.1.14 变更（相对 v10.1.13）—— 两阶段工作流与导出校验收拢

- **学生侧只保留两个阶段**：Phase 1（Task v1 + Agent v1）与 Phase 2（Task v2 +
  Agent v2 + 实验报告 + review-comments）。两个阶段都由课程组直接发放可编辑压缩包，
  学生解压后原地修改，镜像职责收拢为「运行学生内容 + 校验最终导出」。
- **删除 Final Report**：`final_report.md` / Final Report scaffold / 自动生成 /
  完整性校验全部移除；`report/final_report.md` 不再是任何阶段的合法内容。
- **删除学生侧 Phase 2 scaffold**：`exposure` 不再自动生成 reviews/ 与 Final Report；
  旧 `reviews/*.json` 多文件协议删除，唯一审稿记录协议为 **`review-comments.json`**
  （单文件顶层字典；每个分配的匿名 Task 恰好一个条目：`task_id` / `score` 1~5 整数 /
  非空 `comments`；key 与内层 `task_id` 必须一致；原始 JSON 重复 key 在解析层检出
  （`DUPLICATE_REVIEW_TASK_ID`）；条目集合必须与 `assignment.json` 分配完全一致
  （`MISSING_REVIEW_TASK` / `UNKNOWN_REVIEW_TASK`；数量以分配为准，不硬编码 10））。
- **Phase 2 新增两个正式学生材料**：`实验报告.docx`（基于课程 Word 模板填写；只做
  存在 / 非 0 字节 / 能打开的基础校验）与 `review-comments.json`（严格校验）。
  新增错误码：`MISSING/INVALID_EXPERIMENT_REPORT`、`MISSING_REVIEW_COMMENTS`、
  `REVIEW_COMMENTS_NOT_OBJECT`、`INVALID_REVIEW_SCORE`、`INVALID_REVIEW_COMMENTS`、
  `REVIEW_FIELD_MISSING/UNKNOWN`、`REVIEW_TASK_ID_MISMATCH`、`MISSING/INVALID_ASSIGNMENT`。
- **阶段自动识别**：`datac submission build` 的 `--phase` 变为可选；缺省从 workspace
  内 `assignment.json` / `review-comments.json` / `实验报告.docx` 自动识别 P2，否则 P1。
  Build 语义保持 validate → PASS → package（校验 FAIL 不生成正式 ZIP）。
- Task v2 / Agent v2 仍为原地修改 `task.json` / `data.csv` / `agent.py`，
  不引入 `task_v2.json` / `agent_v2.py` 等新文件名。

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
