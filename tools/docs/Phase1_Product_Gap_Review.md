# Phase 1 Product Gap Review

> 教师/开发者参考。回答：**按照官方《学生手册.md》操作，当前 Main 系统为什么不能完整支持学生流程**。
> 事实来源：BigData0929 `src/codesetarena/main/` 当前代码 + 本机 CLI 实测（2026-10-02）。

---

## 1. Student Requirement Table（官方手册步骤 → 当前系统支持 → 差距）

官方手册是 MVP 时代协议（`agentkit` / `OUTPUT_DIR/final.json` / `TXX` 目录树 /
`private/` / 8 次调用预算 / Validity Gate / judge.py）。将其每一步映射到当前 Main：

| Step | 学生动作 | 手册要求（MVP） | 当前系统支持（Main） | 差距 |
|---|---|---|---|---|
| 0 | 遵守统一协议 | 每次 run 最多 8 次 LLM 调用 / 20,000 tokens / 150s；`agentkit.llm()` | 无调用次数上限（观测性）；token 由 `.env LLM_TOKEN_BUDGET`（默认 10000）；墙钟 90s+10s；无 `agentkit` | **协议整体不一致** |
| 0 | 答案输出 | `OUTPUT_DIR/final.json` `{"answer":"<字符串>"}` | 无 final.json；答案为 `run()` 返回值 + `runs/<id>/result.json` | **协议整体不一致** |
| 0 | 判分规则 | 数值容差 ≤1e-6；字符串去空白+忽略大小写 | `_answers_equal`：数值可用 float 归一化相等；字符串 strip 相等（**大小写敏感**）；bool 隔离 | 判分口径不同（学生按旧规则自测会误判） |
| 0 | 隔离 | `private/` 解题方不可见 | 无 private 概念；gold 在 `reference.py`（学生自己写） | 隔离模型不同 |
| 1.1 | 选题与标注 | DA01-12 选 1 Primary+0-3 Secondary；FM01-14 标注（taxonomy.py） | 无 taxonomy.py 学生入口；`expected_failure_mode` 校验两位码 FM01-14；DA 用 `capability` 中文（不校验） | 标注机制存在但位置/形式不同；Secondary FM 无字段 |
| 1.2 | 准备公开数据 | `data/` 目录，多个非空 CSV | 单一 `data.csv`（pandas 可读） | 结构不同（1 文件 vs 多文件+目录） |
| 1.3 | 写题面 | `v1/question.md`（自包含、Validity Gate 自动检查） | 题面写入 `task.json.question`（自由文本，无自包含检查） | question.md 不存在；无 URL/路径检查 |
| 1.4 | 写 task.json | `{task_id, task_version, answer_type, data_files, topic}` | `{task_id, student_id, question, answer_type, declared_answer, expected_failure_mode}` | schema 完全不同 |
| 1.5 | 写标准答案 | `private/answer.json` | 无 answer.json；gold 唯一来源 `reference.py` | **结构不存在**（等效物是 reference.py） |
| 1.6 | 写参考解答 | `private/reference_solution.py`（cwd=任务根、打印 `RESULT =`、60s、stdlib） | `reference.py`（`reference(df)` 单输入单输出、仅 pandas/decimal、纯内存、1s） | 接口不同（无 RESULT= 行协议） |
| 1.7 | 写失败模式报告 | `private/author_report.md` 七段式 | 无任何报告机制/字段 | **缺失** |
| 2 | 打包 Task 包 | `TXX/{data, v1, private}` 目录树 zip | export 单 zip：`.env.example/agent/task/data.csv/reference/metadata/export_manifest`（扁平，无 private） | 包结构完全不同 |
| 3.1 | 选定策略 | direct/standard/verify 三选一写 `agent.yaml` | 无策略枚举、无 agent.yaml | **缺失**（算法自由，协议统一） |
| 3.2 | 实现 agent.py | 环境变量 `TASK_DIR/OUTPUT_DIR/RUN_DIR`；`agentkit.llm/run_code/submit` | `run(task, df, llm)` 三参；`llm.complete(messages)`；返回值即答案 | 接口完全不同 |
| 3.3 | 写 agent.yaml | `{api_version, agent_id, strategy, versions, entrypoint}` | 无 | **缺失** |
| 4.1 | Task 自检 | Validity Gate 六项（question.md/task.json/answer.json/reference_solution.py） | validation 六文件 + FM + data.csv + run 签名 + reference guardrail | 检查项不同但有等效替代 |
| 4.2 | Agent 自检 | 手工搭 `_selftest` 目录 + 环境变量 + `python agent.py` | `codesetarena run --workspace`（真实全链） | **更简单**（新系统此处更优） |
| 4.2 | 结果自查 | `judge.py` 规则（容差 1e-6） | 终端 `correct=True/False` + result.json | 判分即时可见（新系统更优） |
| 5 | 提交 | `TXX/` zip + `Axx/` zip + 报告 | **一个** workspace zip（同时含 Task+Agent） | 交付物模型不同（1 个 vs 3 个） |
| 5 | 冻结 | 提交即冻结 v1 | 无显式 freeze 机制（export_manifest 哈希仅审计） | 冻结无系统保证 |
| 6 | Phase 2 预告 | 匿名分配陌生题、双方修订、Blind 全矩阵 | 课程流程（Phase 1/2 换 fresh workspace，见 AGENTS.md）；无审稿/修订系统 | 超出学生 CLI 范围（课程层面） |

**结论（表）**：官方手册 19 个步骤中，仅 2 步在当前系统下"更简单或等效"（4.1 自检、4.2 结果自查）；其余均为协议/结构/机制不一致或缺失。**"严格按照官方手册操作"在每一步都会失败**——这不是单个功能缺口，而是手册与当前系统**架构级断层**（MVP 协议 → Main 协议）。

---

## 2. 当前 Main 系统实际具备的能力（学生视角）

- **完整单 workspace 执行链**：`codesetarena run` 完成 Validate→Build→Execute→Evaluate，一次命令出 `correct=True/False` 与三种 outcome（completed / analysis_failure / internal_error）。
- **强制的 Export 验收**：`codesetarena export` 在打包前重新执行；答案错误则拒绝打包（exit 1，明确消息）。
- **包内容控制**：zip 只含 7 个固定文件，`.env` 与 `runs/` 自动排除（实测）。
- **运行记录可审计**：`runs/<id>/{result.json, config.yaml, llm_calls.json}`。
- **资源预算有真实执行**：墙钟/内存/磁盘/token 由 Budget 强制执行，超限归 internal_error 并打印原因。
- **Task 声明校验（部分）**：`expected_failure_mode` 两位码合法性；task.json/data.csv/agent.py 签名/reference.py guardrail。

## 3. 当前缺失的能力（从 GAP 文档 9 条重新分类）

| GAP | 类型 | 是否阻塞提交 | 原因 |
|---|---|---|---|
| 官方手册与 Main 架构断层（协议/目录/判分/预算全面不一致） | **A** | **是** | 学生按唯一官方文档操作，第一步（final.json/agentkit）即失败；必须先同步手册与系统 |
| 无 `init`/脚手架；`Template.zip` 内嵌 agent 为旧 4 参版 | **A** | **是** | 学生无法从空目录得到合法起点；按模板复制即触发签名校验失败 |
| 无 `verify-export`；且教师端 validator 对导出包因缺 `.env` 必拒 | **A** | **是** | 提交物无法在提交前被验证可接受，闭环断裂 |
| FM 文档（码表宣传 FM_简写）与校验（只收两位码）不一致 | **A** | **是** | 学生按文档填写即被拒绝，无法声明任务 |
| metadata 无完整校验（student_id/task_id/question/declared_answer/capability 均不检查） | **A** | **是** | 必填声明漏填/错填也能导出，提交后才暴露 |
| declared_answer 双口径（task.json 声明 vs reference.py 实际判分）无一致性检查 | **B** | 否 | 学生可完成，但需理解"gold 在 reference.py"这一隐藏约定，易自测误判 |
| `run` 退出码不表达 analysis_failure（错答也是 0） | **B** | 否 | 可完成；但面向初学者 feedback 弱（需读 stdout） |
| Sandbox 仅 policy 声明、无运行时强制 | **B** | 否 | 不违规的学生流程不受影响；但"禁止行为"无法自证/无拦截反馈 |
| 无学生 FM 命中反馈 | **C** | 否 | truth-table 审计属教师侧高级分析，不影响 Phase 1 完成 |

---

## 4. 为什么当前系统不能完整支持学生流程（总结）

1. **手册层**：官方手册描述的是不复存在的 MVP 协议（agentkit/final.json/private/8-call 预算/三策略）。学生照做必然失败——这是最大阻塞项，必须先"以当前系统为准重写手册"（已产出 `学生手册_Phase1_Current.md` 作为替代基线），并与官方手册的**教学要求**（Task 唯一答案、DA/FM 标注、自检习惯、提交冻结）对齐。
2. **起点层**：没有脚手架命令，官方模板包与当前 3 参契约冲突。
3. **声明层**：FM 简写/两位码不一致；student_id 等声明不校验；declared_answer 与 reference.py 双口径。
4. **终点层**：无提交前包复验；教师端 validator 对导出包缺 `.env` 必然失败（导出契约与校验契约不一致）。

修复优先级：**A 类 5 项 → B 类 3 项 → C 类 1 项**（详见 `Phase1_Minimum_Product_Contract.md` 的逐项影响范围）。