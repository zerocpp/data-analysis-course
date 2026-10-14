# 《大数据分析技术》DataChallenging 助教端操作手册

## 1. 本手册是做什么的

本手册面向负责《大数据分析技术》课程实践环节的助教。

目标是保证以后不同学期、不同助教接手课程后，都可以按照统一流程完成：

```
收集学生 Phase 1 提交
        ↓
批量 Ingest
        ↓
建立 Canonical Repository
        ↓
生成匿名任务分配
        ↓
导出 Exposure 包
        ↓
按学生发放
        ↓
学生使用 Agent v1 测试 10 个匿名 Task
        ↓
进入 Phase 2
```

助教端最重要的原则是：

> **不要手工修改学生 Submission，不要手工拆包，不要手工随机分配 Task。统一通过 `bin/datac` 完成。**

这样才能保证课程每年都可以复用。

------

# 2. 助教端需要理解的三个对象

助教不需要掌握所有系统内部实现，但必须理解三个核心对象。

### Student Submission

学生在 Phase 1 提交的正式课程包。

它包含：

```
Task v1
Agent v1
Metadata
```

助教收到以后，不直接拿里面的文件人工操作，而是交给：

```
datac submission --mode ingest
```

统一处理。

------

### TA Repository

助教端统一课程仓库：

```
/workspace/ta_repository
```

它不是学生工作区。

它负责保存：

```
合法 Task
合法 Agent
学生私有身份映射
匿名 Task ID
Assignment
Exposure distributions
Quarantine
```

整个匿名任务分配都从 Repository 产生。

------

### Exposure Package

助教最终发给学生的匿名任务包。

每个学生收到：

```
10 个其他学生设计的匿名 Task
```

用于：

```
Agent v1 × 10 Tasks
        ↓
观察成功 / 失败
        ↓
分析 Agent 弱点
        ↓
改进 Agent v2
```

Exposure 不是第三次 Submission。

------

# 3. 整个助教端工作流

建议手册开头直接放这一张流程。

```
学生 Phase 1 提交包
        ↓
received_v1016/
        ↓
Submission Ingest
        ↓
TA Repository
        ↓
Canonical Tasks + Agents
        ↓
Anonymous Task IDs
        ↓
Assignment Prepare
        ↓
AR000001
        ↓
Export Exposure
        ↓
BigData_Exposure_Rxxxxxx.zip
        ↓
按照私有映射发给对应学生
        ↓
学生 Exposure Import
        ↓
Agent v1 × 10
```

助教真正需要执行的核心命令只有三组：

```
1. ingest
2. assignment prepare
3. export-exposure
```

------

# 4. Step 1：收集学生 Phase 1 Submission

先创建统一收件目录：

```
mkdir -p /workspace/received_v1016
```

将本届所有学生 Phase 1 的正式提交包复制到：

```
/workspace/received_v1016/
```

例如：

```
received_v1016/
├── 学生A提交包
├── 学生B提交包
├── 学生C提交包
├── ...
└── 学生N提交包
```

这里强调：

> 只放学生正式生成的 Submission 包。

不要混入：

```
原始 workspace
截图
实验报告单文件
临时 ZIP
Exposure 包
```

------

# 5. Step 2：Ingest 学生 Submission

进入课程 Release 根目录后执行：

```
bin/datac submission --mode ingest \
  --input /workspace/received_v1016 \
  --output /workspace/ta_repository
```

这一条命令完成的是：

```
收到学生包
   ↓
逐包检查
   ↓
合法包拆解
   ↓
Task / Agent 标准化
   ↓
建立 Repository
```

助教不要手动解压学生包。

------

## 5.1 Ingest 会做什么

助教只需要理解它的结果。

对于合法 Submission：

```
Student Submission
        ↓
Canonical Task
Canonical Agent
Private Identity Registry
Anonymous Task Mapping
```

对于不合法 Submission：

```
Validation FAIL
        ↓
Quarantine
```

即隔离到类似：

```
/workspace/ta_repository/
└── quarantine/
    └── single_validation_failed/
```

不会因为一个坏包导致整个班级 Ingest 中断。

------

## 5.2 助教首先检查 Summary

执行完成后重点看：

```
ingested = ?
single_fail = ?
```

理想状态：

```
ingested=<本班提交人数>
single_fail=0
```

如果：

```
single_fail > 0
```

不要马上执行最终匿名发放。

先检查：

```
ta_repository/quarantine/single_validation_failed/
```

确认是哪一位学生的 Submission 没有通过。

------

# 6. Step 3：生成匿名审稿 / Exposure 分配

Ingest 完成以后执行：

```
bin/datac assignment --mode prepare \
  --repository /workspace/ta_repository \
  --phase phase1 \
  --seed 20261009
```

其中：

```
--repository
```

指定助教仓库。

```
--phase phase1
```

表示使用第一阶段 Submission。

```
--seed 20261009
```

用于保证分配结果可复现。

------

# 7. 为什么必须保留 Seed

Seed 是课程复用的重要参数。

例如：

```
--seed 20261009
```

只要：

```
Repository 不变
+
Seed 不变
```

则重新执行分配时应得到相同结果。

因此助教必须记录本届课程使用的：

```
Academic Year
Course Version
Assignment Seed
```

例如：

```
课程版本：
BigData v10.1.16

阶段：
phase1

Assignment Seed：
20261009
```

下一届课程可以重新指定新的 Seed。

------

# 8. Assignment Prepare 的关键产物

成功后系统会产生一个 Assignment Run，例如：

```
AR000001
```

目录可能类似：

```
ta_repository/
├── registry/
│   └── assignments.jsonl
│
├── assignments/
│   └── AR000001/
│       ├── private_mapping.json
│       ├── assignment.npy
│       └── assignment.csv
│
└── distributions/
    └── AR000001/
        └── reviewers/
```

------

## 最重要：private_mapping.json

这是助教私有信息。

它负责建立：

```
匿名 Reviewer ID
        ↕
真实学生身份
```

例如：

```
R000001 → 学生 A
R000002 → 学生 B
```

这个文件：

> **只能由教师/助教保存，绝对不能放入学生 Exposure 包。**

------

# 9. Step 4：导出学生 Exposure 包

Assignment 成功后执行：

```
bin/datac assignment --mode export-exposure \
  --repository /workspace/ta_repository \
  --assignment-run AR000001
```

这里：

```
AR000001
```

必须和上一步生成的 Assignment Run 一致。

不要每次凭感觉修改。

------

# 10. Exposure 导出结果

系统会产生：

```
ta_repository/
└── distributions/
    └── AR000001/
        └── exposure/
            ├── BigData_Exposure_R000001.zip
            ├── BigData_Exposure_R000002.zip
            ├── BigData_Exposure_R000003.zip
            └── ...
```

正常情况下：

```
Exposure ZIP 数量
=
本次参与 Assignment 的学生数量
```

每个 Exposure 包中：

```
assigned_tasks = 10
```

即每位学生收到 10 个匿名 Task。

------

# 11. 发放前必须检查的三件事

建议把这部分做成助教 Checklist。

| 检查项                | 正常结果                         |
| --------------------- | -------------------------------- |
| Ingest                | `single_fail=0` 或异常学生已处理 |
| Assignment            | `status=READY`                   |
| Exposure 数量         | 与参与学生人数一致               |
| 每人 Task 数          | 10                               |
| 学生身份              | Exposure 中无作者身份            |
| Gold / Answer         | 不向学生泄漏                     |
| Failure Mode 私有信息 | 不向 Agent 泄漏                  |

特别强调：

> 不允许把 `private_mapping.json` 一起发给学生。

------

# 12. Step 5：按 Mapping 发给学生

助教根据：

```
private_mapping.json
```

确认：

```
R000001
```

对应哪名学生。

然后将：

```
BigData_Exposure_R000001.zip
```

发给对应学生。

学生不需要知道：

```
Task 原作者是谁
Task 原来的 student_id
```

------

# 13. 学生收到以后怎么运行

助教应该知道学生侧操作，这样以后能排查问题。

学生执行：

```
bin/datac exposure import \
  --package /workspace/BigData_Exposure_R000001.zip \
  --workspace /workspace
```

然后：

```
bin/datac exposure run \
  --workspace /workspace
```

预期：

```
Agent v1
×
10 Anonymous Tasks
```

最终运行结果一般保存在：

```
runs/exposure/
```

学生利用这些结果改进自己的 Agent。

------

# 14. Phase 1 和 Phase 2 的测试逻辑完全复用

这一点在助教手册里一定要强调。

系统没有：

```
Phase1Runner
Phase2Runner
```

两套不同逻辑。

两阶段复用同一个：

```
Reference
Runtime
Agent
Evaluator
```

只是通过：

```
--phase
```

选择不同工作区。

------

# 15. Phase 1 测试

标准答案测试：

```
bin/datac reference run --phase phase1
```

Agent 测试：

```
bin/datac run --phase phase1
```

系统使用：

```
workspace/phase1/
```

中的：

```
task.json
data.csv
reference.py
agent.py
metadata.json
```

------

# 16. Phase 2 测试

标准答案测试：

```
bin/datac reference run --phase phase2
```

Agent 测试：

```
bin/datac run --phase phase2
```

系统使用：

```
workspace/phase2/
```

中的最终版本。

因此：

```
phase1
→ Task v1 + Agent v1

phase2
→ Task v2 + Agent v2
```

但运行实现完全相同。

------

# 17. 为什么两阶段必须复用同一套逻辑

助教以后维护课程时，不要复制代码建立：

```
phase1_run.py
phase2_run.py
```

课程设计原则是：

```
same runtime
same evaluator
same validator
different phase input
```

也就是：

```
bin/datac run --phase phase1

               与

bin/datac run --phase phase2
```

唯一变化应该主要是：

```
输入工作目录
```

而不是 Runtime 规则。

这样课程才能真正复用。

------

# 18. 助教遇到问题时如何定位

建议以后助教统一按照层次排查，而不是直接改代码：

```
第一层：Submission 有没有问题？
        ↓
submission ingest

第二层：Repository 是否正常？
        ↓
Task / Agent 是否成功入库

第三层：Assignment 有没有 READY？
        ↓
ARxxxxxx

第四层：Exposure 是否生成？
        ↓
BigData_Exposure_Rxxxxxx.zip

第五层：学生能否 import？
        ↓
exposure import

第六层：Agent 能否跑 10 Task？
        ↓
exposure run
```

这样能快速判断问题到底发生在：

```
学生提交
课程入库
分配
匿名化
导出
学生导入
Runtime
```

哪个阶段。

------

# 19. 助教不要做的事情

这一部分非常有必要，因为关系到课程能否长期维护：

```
不要手工修改学生 ZIP
不要手工解压后再重新压缩
不要人工重命名 Task ID
不要人工生成 Anonymous Task ID
不要手工随机分配 10 个 Task
不要修改 private_mapping 再发包
不要把 Registry 私有文件发给学生
不要为 Phase 1 / Phase 2 创建两套 Runner
不要因为一个坏包直接删除整个 Repository
```

系统能够处理的事情尽量让：

```
datac
```

来处理。

------

# 20. 建议助教每届课程保存一份 Course Run Record

为了以后真正做到复用，我强烈建议每一届课程结束以后保存：

```
course_run_record/
├── COURSE_VERSION.txt
├── ASSIGNMENT_SEED.txt
├── RELEASE_VERSION.txt
├── INGEST_SUMMARY.txt
├── ASSIGNMENT_RUN.txt
└── README.md
```

例如：

```
Course:
大数据分析技术

Release:
BigData v10.1.16

Phase1 Received:
300

Phase1 Ingested:
300

Single Validation Failed:
0

Assignment Seed:
20261009

Assignment Run:
AR000001

Exposure Packages:
300
```

下一届助教看这一个文件，就知道上一届到底是怎么跑的。

------

# 21. 助教最需要记住的三条正式命令

手册最后可以单独放一页“速查”。

```
# 1. 收包入库
bin/datac submission --mode ingest \
  --input /workspace/received_v1016 \
  --output /workspace/ta_repository
# 2. 生成匿名分配
bin/datac assignment --mode prepare \
  --repository /workspace/ta_repository \
  --phase phase1 \
  --seed 20261009
# 3. 导出 Exposure
bin/datac assignment --mode export-exposure \
  --repository /workspace/ta_repository \
  --assignment-run AR000001
```

助教端最核心的操作实际上就是：

```
INGEST
  ↓
PREPARE
  ↓
EXPORT EXPOSURE
```

------

# 22. 两阶段运行命令速查

学生 / 助教排查 Phase 1：

```
bin/datac reference run --phase phase1
bin/datac run --phase phase1
```

学生 / 助教排查 Phase 2：

```
bin/datac reference run --phase phase2
bin/datac run --phase phase2
```

一句话记忆：

> **Phase 不决定执行逻辑，只决定本次读取哪个阶段的工作区。**

------

# 23. 我建议助教手册最终目录

如果你接下来要正式做成 Markdown / Word，我建议目录就冻结成下面这样：

```
《大数据分析技术 DataChallenging 助教端手册》

1. 手册定位与课程复用原则
2. 助教需要理解的课程对象
3. 课程整体 TA 工作流
4. 课程 Release 与工作目录
5. Phase 1 学生提交收集
6. Submission Ingest
7. Quarantine 与异常 Submission 处理
8. Canonical Repository
9. 匿名化机制
10. Assignment Prepare
11. Seed 与结果复现
12. Assignment Run 与私有 Mapping
13. Exposure Export
14. Exposure 发放
15. 学生 Exposure Import / Run
16. Phase 1 Reference / Agent 测试
17. Phase 2 Reference / Agent 测试
18. 两阶段 Runtime 复用原则
19. 常见异常与排查路径
20. 每届课程运行记录
21. 助教命令速查
22. 课程结束归档
```

这会比单纯写“助教怎么发 10 个 Task”更有价值，因为你真正要解决的是：

> **今年是你在维护课程，但明年换一个完全没参与开发的助教，他也能仅靠 Release + TA Manual 把整门课重新跑起来。**

因此助教手册的核心关键词应该是：

```
可复现
可交接
少人工
统一 CLI
私有身份隔离
异常可追踪
阶段逻辑复用
```

而不是系统内部类、函数、Scheduler 或源码结构。