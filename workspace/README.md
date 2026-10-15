# BigData 两阶段 Workspace（v10.1.16 冻结布局）

```
phase1/    Task v1 + Agent v1（task.json / data.csv / reference.py / agent.py / metadata.json）
phase2/    Task v2 + Agent v2 + review-comments.json + 实验报告.docx
exposure/  助教分发的匿名 Task（assignment.json + ATxxx/）
runs/      运行证据（phase1 / phase2 / exposure/ATxxx）
store/     系统包与 Build 中间产物（packages / builds）
.env       三阶段共用的唯一运行配置（永不进入提交包）
```

## 1. 配置环境

```
cp .env.example .env       # 填 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL（三项必填）
bin/datac doctor
```

## 2. Phase 1（Task v1 + Agent v1）

```
# 编辑 phase1/task.json（只写 description / task_statement / answer；
# answer 必须等于 reference.py 的实际结果。hypothesized_failure_mode 在
# metadata.json 中管理，使用无数字编号的 canonical FM 键）
bin/datac reference run --phase phase1     # 核对 answer
bin/datac run --workspace . --phase P1     # 运行自己的 Task -> runs/phase1/
```

## 3. 匿名 Task（Exposure）

```
bin/datac exposure import --package BigData_Exposure_xxx.zip --workspace .
# 匿名 Task 落 exposure/ATxxx/；你自己的 Task 永远不会被覆盖
bin/datac exposure run --workspace .       # 固定使用 phase1/agent.py（Agent v1）
# 证据：runs/exposure/ATxxx/
```

## 4. Phase 2（Task v2 + Agent v2 + Review + 报告）

```
# 编辑 phase2/ 五件套；填写 review-comments.json（key = 分配的 AT 编号）
# 与 实验报告.docx
bin/datac reference run --phase phase2
bin/datac run --workspace . --phase P2     # -> runs/phase2/
```

**review-comments.json 纪律：每一道分配给你的题写一条**（key = 匿名题号，
与 `exposure/assignment.json.assigned_tasks` 完全一致；score 为 1~5 整数；
comments 非空）。文件内已附模板条目，替换示例后逐题补全。

## 5. Build（每阶段一个正式总包）

```
bin/datac build --phase P1 --workspace . --student-id 你的学号
#  -> store/packages/bigdata_p1_<学号>.tar.gz（严格 3 个一级文件）
bin/datac build --phase P2 --workspace . --student-id 你的学号
#  -> store/packages/bigdata_p2_<学号>.tar.gz（严格 5 个一级文件）
```

红线：exposure 永不覆盖 phase1/phase2；Exposure 永远用 Agent v1；
P1 只读 phase1，P2 只读 phase2；.env / runs / exposure / store 永不入包。
