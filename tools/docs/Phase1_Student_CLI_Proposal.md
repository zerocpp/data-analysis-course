# Phase 1 Student CLI Proposal

> 教师/开发者参考。定义：**修复后学生最终应该如何操作**（≤5 个命令）。
> 现状命令：`codesetarena run` / `codesetarena export`（已实测）。
> 新增建议：`init` / `validate` / `verify-export`（设计，见 Minimum Contract）。

---

## 1. 目标与约束

- 学生流程控制在 **5 个命令以内**：`init → validate → run → export → verify-export`。
- 不引入：Git / GUI / 数据库 / 账号系统 / Web 平台（Phase 1 只需 CLI + Workspace + Validator + Export）。
- 与现有命令完全兼容：`run` / `export` 参数与行为不变（仅 run 退出码语义 B 类调整待评审）。

## 2. 命令规范

### 2.1 codesetarena init

```bash
codesetarena init --workspace ./my_phase1
```

- 新建目录并生成 6 个必需文件（task.json / data.csv / agent.py / reference.py / .env / .env.example）。
- 目录已存在且非空 → 报错退出（不覆盖）。
- 输出：生成的路径清单 + 下一步提示（"编辑后运行 codesetarena validate"）。

### 2.2 codesetarena validate

```bash
codesetarena validate --workspace ./my_phase1
```

- 复用真实 `validate_project` + 提交完整性检查（metadata 必填、FM 两位码、declared_answer ↔ reference.py 一致性）。
- **不运行 Agent**；reference.py 至多执行 1 次（秒级）。
- 退出码：0=全部通过；1=存在错误（逐条打印）。

### 2.3 codesetarena run（现有，不变）

```bash
codesetarena run --workspace ./my_phase1
```

- Validate→Build→Execute→Evaluate 全链；输出 `correct=True/False` 与 outcome。
- （B 类建议）退出码：completed=0；analysis_failure≠0；internal_error≠0。

### 2.4 codesetarena export（现有，不变）

```bash
codesetarena export --workspace ./my_phase1
```

- 打包前强验收（答案错误拒打包）；产出 `export/<name>_<时间戳>.zip`。

### 2.5 codesetarena verify-export

```bash
codesetarena verify-export --workspace ./my_phase1   # 先 export 再复验
codesetarena verify-export --archive ./x.zip         # 直接复验已有 zip
```

- 模拟教师侧：解包 → 教师规则重校验（env 来源 `.env.example`）→ 包内容检查（无 .env/runs、哈希一致、manifest 可解析）。
- 退出码：0=教师端将接受；1=列出全部拒绝原因。

## 3. 命令与需求覆盖

| 学生需求 | 命令 | 阶段 |
|---|---|---|
| 从空目录起步 | init | A 类（修复后） |
| 提交前声明/一致性检查 | validate | A 类（修复后） |
| 本地执行与迭代 | run | 现已有 |
| 打包 | export | 现已有 |
| 提交前包复验 | verify-export | A 类（修复后） |

## 4. Fresh Student Simulation

### 4.1 现状（不修复）模拟

| Step | 命令 / 动作 | 输入 | 输出 | 人工介入？ |
|---|---|---|---|---|
| 1 | 打开课程工程，`codesetarena --help` | 课程仓库 | run/export/matrix 列表 | 无 |
| 2 | 开始 Phase 1：**无 init** → 手工 mkdir + 手写 6 文件 | 手册（新） | 6 个文件 | **是**：需阅读手册理解必需文件；模板 zip 复制即失败（4 参 agent，必须人工改写）→ **[PRODUCT GAP]** |
| 3 | 运行 `codesetarena run` | workspace | correct=True/False；errors | 无 |
| 4 | 修改重跑 | agent.py | 新 run_id | 无 |
| 5 | `codesetarena export` | workspace | zip 或 evaluation_failed | 无 |
| 6 | 提交前确认教师端接受：**无 verify-export** → 只能手工 unzip -l + 猜测 | zip | 人工清单核对 | **是**：无法用公开 CLI 确认 → **[PRODUCT GAP]** |
| 7 | 提交 zip | zip | 平台/老师流程 | 否（教学流程） |

**现状结论**：Step 2 与 Step 6 必须人工介入；Step 2 的模板还是坏的（Template.zip 4 参 agent）。→ 未满足"只依赖公开 CLI"。

### 4.2 修复后模拟

| Step | 命令 / 动作 | 输入 | 输出 | 人工介入？ |
|---|---|---|---|---|
| 1 | `codesetarena init --workspace ./p1` | — | 6 个文件清单 + 提示 | 无 |
| 2 | 编辑 task.json（学号/题面/FM 两位码） | 学号、题目 | — | 无（纯编辑，手册给示例） |
| 3 | 编辑 data.csv | 数据 | — | 无 |
| 4 | 编辑 reference.py（标准答案） | 答案 | — | 无（手册解释 gold 来源） |
| 5 | 编辑 agent.py（run 三参） | 解法 | — | 无 |
| 6 | `codesetarena validate --workspace ./p1` | workspace | 0/1 + 逐条错误 | 无 |
| 7 | `codesetarena run --workspace ./p1` | workspace | correct=True/False（退出码表达） | 无 |
| 8 | 若 correct=False：编辑 agent.py → 重复 7 | agent.py | 新 run_id | 无 |
| 9 | `codesetarena export --workspace ./p1` | workspace | zip 路径或 evaluation_failed | 无 |
| 10 | `codesetarena verify-export --workspace ./p1` | zip | PASS（教师端将接受）| 无 |
| 11 | 提交 zip | zip | 平台/老师流程 | 否 |

**修复后结论**：全程 5 个公开命令 + 纯文件编辑，**零源码阅读、零开发命令、零手工 zip、零内部目录修改**，闭环成立。

## 5. 明确不做什么

- 不做 `codesetarena template` 子命令（由 init 内置模板承担）。
- 不做交互式向导（保持一步一命令可脚本化）。
- 不把 matrix / judge / truth-table 暴露给学生。
- 不在 init 里写入真实 API key（.env 留空由课程填充；key 由平台/老师分发）。

## 6. 兼容性与迁移

- 新增命令均不改变 `run`/`export` 现有参数；旧工作目录无需迁移（validate/init 幂等）。
- `init` 生成的 workspace 与手工创建的合法 workspace 结构完全一致（REQUIRED_FILES）。
- 发布顺序建议：同步改手册与码表（FM 两位码）→ init → validate → verify-export →（B 类）run 退出码 →（B 类）sandbox 强制。