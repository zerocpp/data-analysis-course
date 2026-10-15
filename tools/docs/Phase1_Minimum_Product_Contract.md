# Phase 1 Minimum Product Contract

> 教师/开发者参考。定义：**修复后 Phase 1 最低产品应具备什么**。
> 原则：不引入 Git / 版本控制 / GUI / 数据库 / 账号系统 / Web 平台。
> 学生只需：**理解任务 → 写 Task → 写 Agent → 执行 CLI → 提交**。
> 载体：CLI + Workspace + Validator + Export。

---

## 1. 最小产品能力定义

| 能力 | 是否存在（现状） | 最低要求（修复后） |
|---|---|---|
| 从空目录得到合法起点 | ✗ | `codesetarena init` |
| 提交前声明检查（不改/不跑 Agent） | 部分（validation 只管 6 文件+FM+签名） | `codesetarena validate`（补 metadata 必填与一致性） |
| 本地执行与迭代 | ✓ `run` | 不变；补 `analysis_failure` 退出码表达（B 类） |
| 打包 | ✓ `export` | 不变 |
| 提交前包级复验 | ✗ | `codesetarena verify-export` |
| FM 声明单一事实 | ✗（文档与校验不一致） | 输入/校验统一两位码 FM01–FM14 |
| 模板与契约同步 | ✗（Template.zip 旧 4 参） | init 内置官方模板；废弃不兼容分发物 |

---

## 2. 五个设计问题

### 2.1 是否需要 init？—— **需要**

学生无法从空目录开始（手工 6 文件 + 旧模板不兼容）。设计：

```bash
codesetarena init --workspace ./my_phase1
```

生成（以当前 REQUIRED_FILES 为准）：

```text
my_phase1/
├── task.json        # 模板：task_id/student_id/question/answer_type/declared_answer/expected_failure_mode=FM01
├── data.csv         # 最小示例（2 行）
├── agent.py         # 3 参 run(task, df, llm) stub（返回 len(df)）
├── reference.py     # reference(df) 模板（返回 len(df)）
├── .env             # 占位（LLM_* 三键，留空由课程 key 填充）
└── .env.example     # 与 .env 同步
```

不生成 metadata.json（由 run/export 的 build 阶段自动写）。

### 2.2 是否需要 validate？—— **需要**

```bash
codesetarena validate --workspace ./my_phase1
```

- 复用真实 `validate_project`；
- 追加**提交完整性**检查：`task_id`/`student_id`/`question`/`declared_answer` 存在且非空；`expected_failure_mode` ∈ 两位码集合；`declared_answer` 与 `reference.py` 实算结果一致（执行 reference，1 次、纯内存、1s 上限）；
- 纯静态 +reference 一次执行，**不运行 Agent**，供学生快速试错。

### 2.3 是否需要 verify-export？—— **需要**

```bash
codesetarena verify-export --workspace ./my_phase1            # 对 workspace：先 export 再复验
codesetarena verify-export --archive ./x.zip                  # 对已有 zip
```

模拟教师侧：解包到临时目录 → 以**教师规则**重校验（env 来源切换为 `.env.example`，即修复"导出包缺 .env 必拒"问题）→ 检查包内容（无 `.env`/`runs/`；`metadata.json` 哈希与包内文件一致；`export_manifest.json` 可解析）。输出 PASS/FAIL 及原因。

### 2.4 是否需要模板同步机制？—— **由 init 承担，不单独建命令**

- init 内置官方模板（代码内嵌最小合法样例），不再依赖手工解压 zip；
- **废弃/下线不兼容的 `Template.zip`**（其 4 参 agent 与当前契约冲突），或将其内容同步为 3 参版本；
- 不新增 `codesetarena template` 子命令（避免命令膨胀）。

### 2.5 FM metadata 是否统一？—— **统一为两位码**

- **输入/校验唯一形式：`FM01`–`FM14` 两位码**（与当前 validation 一致，实测通过）；
- slug 形式（`FM05_JOIN`）仅作为文档展示名与 truth-table 输出名，**不作为输入**；
- 同步修订 `码表.md / 码表1002.md` 与官方手册，消除"简写可填"的误导；
- `validate` 的错误消息已列全 14 个合法码（现状已具备，保持）。

---

## 3. 逐项修复的影响范围

### 3.1 codesetarena init

```json
{
 "feature": "codesetarena init",
 "problem": "学生无法从空目录开始；官方 Template.zip 内嵌 4 参 agent 与当前 3 参契约冲突",
 "files": ["main/init.py", "main/__main__.py", "tests/test_init.py"],
 "student_flow_before": "手工 mkdir + 手写 6 文件；复制模板即触发 run 签名校验失败",
 "student_flow_after": "codesetarena init --workspace ./my_phase1 → 目录合法、run 直接可跑",
 "tests_needed": ["init creates a workspace that passes validate_project",
                  "init workspace runs and completes (correct=True)",
                  "init refuses to overwrite a non-empty directory"]
}
```

### 3.2 codesetarena validate

```json
{
 "feature": "codesetarena validate",
 "problem": "student_id/task_id/question/declared_answer 无校验；declared_answer 与 reference.py 双口径",
 "files": ["main/validate_cmd.py", "main/__main__.py",
           "main/validation.py(追加必填字段检查)", "tests/test_validate_cmd.py"],
 "student_flow_before": "漏填学生信息也能 run/export，提交后才暴露；自测按 declared_answer 判对错但系统按 reference.py",
 "student_flow_after": "提交前一条命令报告全部声明/一致性错误，不运行 Agent，秒级反馈",
 "tests_needed": ["validate passes on valid workspace",
                  "validate reports missing student_id/task_id/question/declared_answer",
                  "validate rejects declared_answer != reference.py result",
                  "validate does NOT execute agent.py"]
}
```

### 3.3 codesetarena verify-export

```json
{
 "feature": "codesetarena verify-export",
 "problem": "无提交前包复验；当前 validator 对导出包因缺 .env 必然失败，教师端也无法直接复验",
 "files": ["main/verify_export.py", "main/__main__.py",
           "main/validation.py(env 来源参数化: env_file)",
           "tests/test_verify_export.py"],
 "student_flow_before": "导出后无法确认教师端会接受；教师解包复验报 missing .env",
 "student_flow_after": "export 后一条命令模拟教师侧校验（env 用 .env.example），提交前即知 PASS/FAIL",
 "tests_needed": ["verify-export passes on exported archive of a correct workspace",
                  "verify-export fails on archive missing declared files",
                  "verify-export detects .env/runs inside archive",
                  "teacher-side validation of extracted archive succeeds (env_file=example)"]
}
```

### 3.4 FM 两位码统一（文档与校验一致化）

```json
{
 "feature": "FM 两位码作为唯一输入形式",
 "problem": "码表宣传 FM_简写，校验只收 FM01-FM14 两位码；学生按文档填即被拒",
 "files": ["码表.md", "码表1002.md", "docs/学生手册_Phase1_Current.md(若需)",
           "（不改 validation 逻辑，错误消息已列出全部合法码）"],
 "student_flow_before": "按文档填 FM05_JOIN → run 拒绝",
 "student_flow_after": "文档、手册、校验三者一致：填 FM04 通过",
 "tests_needed": ["（回归）existing FM01/FM10/FM13 pass, FM15 fail",
                  "（回归）slug form FM02_SCHEMA still rejected (documented)"]
}
```

### 3.5 metadata 必填校验

```json
{
 "feature": "task.json 声明字段必填校验（validate 阶段）",
 "problem": "student_id/task_id/question/declared_answer 漏填也能 run/export",
 "files": ["main/validation.py", "tests/test_validation_metadata.py"],
 "student_flow_before": "漏填学号仍导出通过",
 "student_flow_after": "validate/run 都报『task.json 缺少 student_id』等明确项",
 "tests_needed": ["missing each required field -> specific error",
                  "existing valid workspaces still pass (no regression)"]
}
```

### 3.6 declared_answer ↔ reference.py 一致性（B 类）

```json
{
 "feature": "validate 校验收 declared_answer 与 reference.py 实算一致",
 "problem": "两处填写同一答案，无一致性检查，学生易自测误判",
 "files": ["main/validate_cmd.py(调 _run_reference)", "tests/test_validate_cmd.py"],
 "student_flow_before": "task.json 写对、reference.py 算错不自知",
 "student_flow_after": "validate 明示『reference.py 返回 3 ≠ declared_answer 2』",
 "tests_needed": ["declared mismatch -> FAIL with both values"]
}
```

### 3.7 run 退出码表达 analysis_failure（B 类，需契约变更评审）

```json
{
 "feature": "run 在 analysis_failure 时返回非 0 退出码",
 "problem": "答案错误也 exit 0，学生只看得到 stdout",
 "files": ["main/__main__.py", "tests/test_main.py(CLI 契约回归)"],
 "student_flow_before": "correct=False 但脚本/学生只检查退出码 → 误判成功",
 "student_flow_after": "completed=0；analysis_failure=1（区别于 internal_error 也建议 1 或独立码）；stdout 保持现状",
 "tests_needed": ["run correct -> 0", "run analysis_failure -> 1",
                  "export evaluation_failed 保持 1 不变（回归）"],
 "note": "会影响依赖 exit code 的既有调用方（含教学脚本与部分现有测试），需一次性契约变更并回归"
}
```

### 3.8 Sandbox 运行时强制（B 类，延期项）

```json
{
 "feature": "agent 源码静态安全闸 + 运行环境限制（网络/进程/越界）",
 "problem": "policy 仅声明；禁止行为无拦截、无违规记录",
 "files": ["main/agent_security.py(新)", "main/validation.py(接入)",
           "main/budget.py(worker 兜底)", "policy/(维持声明)"],
 "student_flow_before": "写网络代码照常运行，无任何反馈",
 "student_flow_after": "违规在 validate/run 阶段被明确拒绝并说明",
 "tests_needed": ["forbidden import/call -> validation error",
                  "clean template agent unaffected (regression 60+)"],
 "note": "较高工作量，A 类关闭后实施；Phase 1 完成不受阻"
}
```

### 3.9 学生 FM 命中反馈（C 类，延期项）

```json
{
 "feature": "analysis_failure 时给出『命中/未命中 hypothesized FM』的教师侧审计工具",
 "problem": "学生/教师无法区分『答错』与『以假设的 FM 方式答错』",
 "files": ["tools/(审计脚本，非学生 CLI)", "experiments/(truth-table 已部分具备)"],
 "student_flow_after": "不影响学生 CLI；审计层输出 FM 命中统计",
 "note": "Phase 1 完成不受阻；优先级最低"
}
```

---

## 4. 修复后闭环（Contract 验收标准）

```text
codesetarena init --workspace ./p1        # 0. 合法起点
（编辑 task.json / data.csv / reference.py / agent.py）
codesetarena validate --workspace ./p1    # 1. 声明+一致性秒级检查
codesetarena run --workspace ./p1         # 2. 迭代到 correct=True（退出码表达对错）
codesetarena export --workspace ./p1      # 3. 强验收打包
codesetarena verify-export --workspace ./p1  # 4. 模拟教师侧复验
（提交 zip）
```

验收标准：**学生只使用公开 CLI + 编辑文件，全程无源码阅读、无开发命令、无手工 zip、无内部目录修改，即可从空白走到可提交 ZIP**。