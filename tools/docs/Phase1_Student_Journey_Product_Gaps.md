# Phase 1 Student Journey → Product Function Mapping（教师/开发者参考）

> 本文件与 `学生手册_Phase1_Current.md` 配套，**不要求学生阅读**。
> 用途：判断"当前 DAC 产品功能设计是否合理"。
> 事实来源：BigData0929 当前代码 + 本机实测 CLI 行为（2026-10-02）。

---

## 1. Student Journey Contract（35 问，答案全部来自代码）

| # | 问题 | 代码事实 |
|---|------|----------|
| 1 | 学生第一次拿到什么 | 课程工程目录 + `workspace/Template.zip`（内含 12 个 `system_2724xx.zip` 样例 + `mapping_and_answers.csv`）|
| 2 | 是否需要安装 | 不需要；课程已提供 `.venv`（`pyproject.toml` 注册了 `codesetarena` 命令）|
| 3 | 是否提供虚拟环境 | 是：课程根 `.venv/`（Python 3.12 + numpy + pandas）|
| 4 | 进入哪个目录 | 课程工程根；workspace 建在 `workspace/<自己的目录>/` |
| 5 | 可用 CLI | `codesetarena run --workspace <dir>`；`codesetarena export --workspace <dir>`（`matrix` 是教师命令）|
| 6 | 最小合法 workspace | `validation.REQUIRED_FILES = (.env, .env.example, agent.py, task.json, data.csv, reference.py)`；`.env` 必填 `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL` |
| 7 | Task 放哪 | workspace 根 `task.json` |
| 8 | data.csv 放哪 | workspace 根 `data.csv`（被 `pd.read_csv` 读取）|
| 9 | Agent 放哪 | workspace 根 `agent.py` |
| 10 | Agent 正式入口 | `run(task, df, llm)`，恰好三参（validation 强制），返回值即答案 |
| 11 | 必填字段 | 校验层面 task.json 只要"合法 JSON 对象 + expected_failure*（若有则须合法）"；教学/提交层面要求 `task_id/student_id/question/answer_type/declared_answer/expected_failure_mode` |
| 12 | student_id 填哪 | `task.json` 的 `student_id`（**校验不强制**，仅示例/提交要求）|
| 13 | DA 填哪 | `task.json` 的 `capability`（中文文字，**校验不读取**；DA 归属由教师侧 `mapping_and_answers.csv` 管理）|
| 14 | expected_failure_mode 填哪 | `task.json` 的 `expected_failure_mode`（runner 会写入 result.json）|
| 15 | FM 允许哪些码 | 14 类两位码 FM01–FM14（含 FM10/FM13，taxonomy v2.0）；简写 `FM02_SCHEMA` 形式**校验不通过**（实测）|
| 16 | declared_answer 填哪 | `task.json.declared_answer`（声明用）；**判分 gold 实际来自 `reference.py`**（`_run_reference`）|
| 17 | env/API 配置谁提供 | 课程提供 key；学生写进 workspace `.env`（`.env.example` 为模板）|
| 18 | 学生是否接触 API key | 是（`.env` 中明文管理）；系统保证 key 不进结果产物与 export 包 |
| 19 | 首次验证 | `codesetarena run --workspace ...` |
| 20 | 执行 Agent×Task | `codesetarena run`（validate→build→execute→evaluate）|
| 21 | 成功 artifact | `runs/<run_id>/config.yaml` + `result.json`（+`llm_calls.json` 若有 LLM 调用）；workspace 根 `build_manifest.json`/`metadata.json` |
| 22 | 三种 outcome 学生语义 | completed=正常且正确；analysis_failure=正常但答案错（FM 分析对象）；internal_error=未正常产出答案 |
| 23 | 资源限制 | 墙钟 90s+10s 宽限；内存 2048MB；磁盘 512MB；Token 由 `.env LLM_TOKEN_BUDGET`（默认 10000）；LLM 单请求 75s（policy/runtime_policy.json）|
| 24 | Sandbox 禁止 | policy 声明：任意网络/外部 API/pip/越界读写/symlink/未授权 shell/secret 泄露（**当前仅声明，无运行时强制**）|
| 25 | 修改后重跑 | 直接再 `codesetarena run`；`runs/` 累积历史，不覆盖 |
| 26 | 判断 Task 可解 | reference.py 有确定返回值即存在 gold |
| 27 | 判断 Agent 能解自己 Task | run 后 `correct=True` |
| 28 | 最终 export 命令 | `codesetarena export --workspace ...` |
| 29 | export 生成什么 | `export/<workspace>_<时间戳>.zip` |
| 30 | ZIP 内文件 | `.env.example, agent.py, task.json, data.csv, reference.py, metadata.json, export_manifest.json` |
| 31 | ZIP 内不能有 | `.env`、`runs/`（实测源码 `PACKAGE_FILES` 排除）|
| 32 | 提交前重验证导出包 | **无公开命令**（无 `validate-export`；且当前 `validate_project` 对解包目录因缺 `.env` 报错）|
| 33 | 最终文件名 | 系统自动 `<workspace>_<YYYYMMDD_HHMMSS>.zip` |
| 34 | 提交到哪 | 平台/老师指定（系统无提交命令）|
| 35 | 提交后是否冻结 | 教学流程冻结；系统无显式 freeze 机制（export_manifest/哈希可用于审计）|

---

## 2. Student Step → Product Function Mapping

| 学生步骤 | 学生目标 | 当前公开接口 | 对应代码 | 正常反馈 | 失败反馈 | 是否需要手工绕过 | UX 风险 |
|---|---|---|---|---|---|---|---|
| 创建 Workspace | 有可运行的工作目录 | 无 init 命令 | `validation.REQUIRED_FILES` | 无 | 校验列出缺哪些文件 | 需手工 mkdir + 手写 6 个文件 | 高：无脚手架 |
| 使用模板样例 | 复制可运行起点 | 无（Template.zip 手工解压） | — | 无 | 模板内 agent.py 为 4 参旧版，直接 run 被拒 | 需手工解压多个嵌套 zip + 改写 agent 为 3 参 | 高：Template.zip 与当前契约不兼容 |
| 填身份信息 | student_id/task_id 正确 | 手工编辑 task.json | validation 不读取 | 无 | 无（不会被检查） | 否 | 中：系统不校验，错填也能导出 |
| 设计 Task | 明确的问题 | 手工编辑 task.json `question` | build 透传 task | 无 | 无 | 否 | 中：无任何语法/语义检查 |
| 选 DA | 任务归属能力 | 手工填 `capability` | 不被读取 | 无 | 无 | 否 | 中：隐藏字段，系统无校验 |
| 选 FM | 声明假设失败模式 | 手工填 `expected_failure_mode` | validation 校验两位码 | run 通过 | 明确列出合法 FM 列表 | 否 | 中：码表宣传 FM_简写，校验拒简写（文档/代码不一致） |
| 填 declared answer | 有标准答案 | 手工填 `declared_answer` + 写 `reference.py` | gold=`_run_reference` | 无 | reference guardrail 报错 | 需理解 gold 来自 reference.py | 高：双处填写且无一致性检查 |
| 写 Agent | 可运行入口 | 手工写 `agent.py` | validation 3 参签名检查 | run 通过 | 明确报签名错误 | 否 | 低 |
| 运行 | 验证 Task×Agent | `codesetarena run` | run_project | 终端一行 + exit 0 | exit 2/1 带原因 | 否 | 中：analysis_failure 时 exit code 仍为 0，学生必须读 stdout |
| 看结果 | 知道对错 | 终端行 + `runs/<id>/result.json` | execute_project | correct=True 打印 | correct=False 打印 | 否 | 低 |
| 修改重跑 | 迭代 | 再 run | 新 run_id | 新记录 | 同 run | 否 | 低 |
| 预算自检 | 不超限 | 读 result.json budget | budget.py | stop_reason=completed | internal_error + 原因 | 否 | 中：需要打开 JSON 看数字 |
| Sandbox | 不违规 | 无学生侧可见强制 | policy 声明（无运行时实施） | 无 | 无 | 否 | 高：手册说禁止，系统不拦截，无法自证合规 |
| Export | 得到 ZIP | `codesetarena export` | export_project | exported + 包路径 | evaluation_failed(exit 1) 明确 | 否 | 低 |
| 检查 ZIP | 内容正确 | 手工 `unzip -l` | PACKAGE_FILES | 人工核对 | 无工具 | 需手工核对+理解清单 | 中 |
| 提交前重验证 | 教师端必过 | 无 | — | 无 | 无 | 无法用公开 CLI 完成 | 高：validate-export 缺失 |
| 提交 | 交付 | 无（老师/平台流程） | — | 平台提示 | — | 否 | 由教学流程承担 |
| 冻结 | 提交后锁定 | 无显式机制 | export_manifest.json（含哈希，仅审计） | — | — | 否 | 中：无不可篡改证明 |

---

## 3. [PRODUCT GAP] 清单

- **[PRODUCT GAP] 没有 `codesetarena init`/脚手架命令**：学生必须手工 `mkdir` 并手写 6 个必需文件；没有任何"生成模板 workspace"的入口。
- **[PRODUCT GAP] 官方 Template.zip 与当前契约不兼容**：`workspace/Template.zip` 内嵌的 `system_272401.zip/agent.py` 仍是旧 4 参 `run(task,df,llm,max_steps)`，当前 validation 会直接拒绝（实测：提示签名必须恰好 3 参）。学生按模板操作第一步就失败。
- **[PRODUCT GAP] FM 文档与校验不一致**：`码表.md/码表1002.md` 宣传学生用 `FM05_JOIN` 形式的简写码；实测 validation 只接受两位码 `FM01`–`FM14`，简写码被拒。学生按文档填会被拒绝。
- **[PRODUCT GAP] gold 来源双重口径**：`task.json.declared_answer` 与 `reference.py` 返回值都叫"标准答案"，但判分只认 reference.py；无一致性校验，且学生需要在两处维护同一答案。
- **[PRODUCT GAP] `run` 的退出码不区分 correct/incorrect**：analysis_failure 时 exit code 仍为 0，学生必须读 stdout 的 `correct=False` 才能知道答案错；面向初学者的反馈强度不足。
- **[PRODUCT GAP] 无 `codesetarena validate-export`（导出后重验证）**：学生无法用公开 CLI 确认"教师端会接受我的包"。且现有 `validate_project` 对解包目录因缺 `.env` 必然报错（`.env` 不进包 vs 校验要求 `.env`），教师端直接重校验同样受阻。
- **[PRODUCT GAP] Sandbox 仅声明未实施**：`policy/runtime_policy.json` + `agent_environment.json` 声明了网络/文件系统/进程限制，但当前工程没有运行时强制层（无静态 agent 安全扫描，子进程无系统级隔离）。学生手册写的"禁止行为"当前实际可执行且不产生违规记录。
- **[PRODUCT GAP] 学生元数据无校验**：`student_id`/`task_id`/`question`/`capability`/`declared_answer` 均不被 validation 检查；错填、漏填都能 run/export，提交后才可能暴露。
- **[PRODUCT GAP] 无学生侧 FM 命中审计**：result.json 只记录 `expected_failure_mode`，系统不提供"该 FM 是否真实命中"的学生可见反馈（truth-table 审计在教师侧实验脚本）。

---

## 4. 模板旧内容核对（已废弃，未写入新版手册）

| 旧《学生手册》内容 | 当前代码事实 | 处理 |
|---|---|---|
| `agentkit.llm()` | 不存在；统一 `run(task, df, llm)` 中的 `llm.complete(messages)`（main/llm.py） | 废弃，不沿用 |
| `OUTPUT_DIR/final.json` 协议 | 不存在；答案为 `run` 返回值，结果在 `runs/<id>/result.json` | 废弃 |
| `TASK_DIR / OUTPUT_DIR` 双目录 | 不存在；单 workspace 根目录承载全部 6 文件 | 废弃 |
| 固定 8 次 LLM calls | 调用次数无固定上限（观测性）；预算按累计 token | 废弃 |
| 20,000 tokens / 150 秒 | token 默认 10000（`.env LLM_TOKEN_BUDGET` 可调）；墙钟 90s+10s 宽限 | 废弃 |
| `tasks/TXX/v1/private` 目录树 | 不存在 | 废弃 |
| direct / standard / verify 三策略固定枚举 | 无；Agent 算法自由，课程只统一 `run(task,df,llm)` 协议 | 废弃 |
| 旧 4 参 `run(task,df,llm,max_steps)` | 当前模板/校验为 3 参（.env.example 中旧 `AGENT_MAX_STEPS` 亦已无读取方） | 废弃 |
| 旧 Task/Agent package schema | 现为 REQUIRED_FILES 6 文件 + PACKAGE_FILES 7 项导出 | 废弃 |
| 中文 `capability` 作为标注主体 | 迁移方向为 FM（taxonomy v2.0）；capability 目前无校验 | 部分废弃 |

---

## 5. Final Assessment

**问题**：按照当前学生手册，一个学生能否**仅依赖公开 Student CLI 从空白状态完整走到 Phase 1 ZIP？

**结论：不能完整闭环。** 缺口如下：

1. **起点缺脚手架**：没有 `init`/模板生成命令；且课程提供的 Template.zip 内嵌样例 agent 与当前 3 参契约不兼容，学生按模板第一步即失败（必须手工绕过：改写 agent）。— 缺失环节：**Workspace 初始化**
2. **middle 缺一致性保障**：declared_answer 双口径（task.json vs reference.py）无校验；student_id/capability/FM 简写等易错且部分不被校验。— 缺失环节：**Task 声明校验**
3. **终点缺重验证**：导出后没有任何公开命令让学生确认"教师端会接受"；现有 validator 对解包包因缺少 `.env` 必然拒绝，教师端也无法直接复验。— 缺失环节：**validate-export / 包级复验**
4. **合规不可自证**：Sandbox 是 declaration-only，学生无法通过系统看到"我的 Agent 违规被拦截"的反馈。— 缺失环节：**安全边界反馈**

可正常闭环的部分：`run`（含结果读取与三种 outcome 区分）、迭代重跑、资源预算超限报错、`export` 的强验收（错误答案拒打包）、ZIP 内容控制（自动排除 .env/runs）。

> 上述缺口即 3. [PRODUCT GAP] 清单；建议按 高（init/模板修复 / validate-export / sandbox 强制 / FM 文档一致化）→ 中（exit code 区分 / metadata 校验 / declared_answer 一致性）排序投入产品设计。