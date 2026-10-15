# 统一评测工具（datac eval）

> Teacher / Research 工具：**单次执行 + 批量串行执行 + 自动结果汇总**。
> 三个能力共用同一个现有 Runtime 执行核心，不引入第二套 Agent 执行 / LLM /
> Budget / 判分实现。

## 定位与不变量

- 唯一执行核心：`codesetarena.main.runner.run_project`（与 `datac run` /
  `datac export` / `datac matrix` 完全相同：子进程 + Budget Supervisor +
  BudgetedLLM 累计 Token + reference gold 判分 + `result.json` 落盘）。
- 单次入口：`codesetarena.eval.runner.run_one`（CLI `eval run` 与批量循环
  共用同一函数，批量模式下不允许另行编写 Agent 执行逻辑）。
- 配置合并唯一实现：`codesetarena.eval.config`（run 与 batch 共用）。
- 汇总只信磁盘：`summary.csv/json` 从每条运行实际生成的 `result.json` 读取，
  绝不根据 CLI 返回码猜测。
- API Key 只经**环境变量名**间接提供；绝不入 CLI 明文参数、`result.json`、
  `summary.*`、`execution.log`（运行 workspace 内的 `.env` 是 Connection
  Configuration，与 student workspace / matrix 同一设计）。
- 评测可调参数，但绝不突破 `policy/runtime_policy.json` 强制上限（超出自动
  钳制并在记录中标记 `clamped`）。

## 命令

```bash
# 单次执行
.venv/bin/datac eval run \
  --task  pool/tasks/T000001 \
  --agent pool/agents/A000001 \
  --config eval_configs/deepseek.json \
  --output eval_runs/single_001

# 参数覆盖（只允许 ≤ policy 上限，超出自动钳制）
.venv/bin/datac eval run \
  --task  pool/tasks/T000001 \
  --agent pool/agents/A000001 \
  --config eval_configs/deepseek.json \
  --timeout 10 --token-budget 5000 \
  --output eval_runs/single_override

# 批量串行执行（K Model × N Task × M Agent，Model → Task → Agent 固定顺序）
.venv/bin/datac eval batch \
  --batch-config eval_configs/batch.json \
  --output eval_runs/batch_001
```

`eval run` 额外支持：`--model` / `--base-url` / `--api-key-env` 显式覆盖配置
文件默认值；`--force` 重建已存在输出目录（默认拒绝覆盖）。
`eval batch` 额外支持：`--timeout` / `--token-budget` / `--api-key-env`
批次级统一覆盖（作用在每模型配置默认值之上）；`--resume` 续跑（跳过已完成
组合）；`--force` 整目录重建；`--batch-id` 批次标识。

## 配置文件

模型配置（`examples/eval_configs/*.json`）：

```json
{
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com/v1",
  "api_key_env": "DEEPSEEK_API_KEY",
  "token_budget": 10000,
  "timeout_seconds": 90
}
```

批量配置（`examples/eval_configs/batch.json`）：

```json
{
  "tasks": ["pool/tasks/T000001", "pool/tasks/T000002"],
  "agents": ["pool/agents/A000001", "pool/agents/A000002"],
  "model_configs": ["eval_configs/deepseek.json", "eval_configs/qwen.json"],
  "batch_id": "batch_001"
}
```

优先级（`eval run` 与 `eval batch` 共用同一实现）：

```text
系统默认值（policy/runtime_policy.json）
       ↓
配置文件提供默认值（model / base_url / api_key_env / token_budget / timeout_seconds）
       ↓
CLI 显式参数覆盖（未传参不覆盖：argparse 默认 None）
       ↓
强制 Policy 钳制（timeout ≤ agent.wall_clock_seconds；token_budget ≤
llm.token_budget_per_execution；超出即钳制 + clamped 标记）
       ↓
最终有效 Runtime Config
       ↓
传入现有 Runtime（连接字段 → workspace .env → _resolved_config → LLMClient；
资源字段 → Budget(timeout_seconds, max_tokens) → run_with_budget）
```

说明：`supervisor_timeout_seconds = 业务时限 + policy main.boot_grace_seconds`
（与课程 `_execution_budget` 同一语义，安全垫覆盖子进程冷启动）；汇总表中
`timeout_seconds` 列记录的是**实际传入 Budget 的 supervisor 时限**。
注：run 目录内 `config.yaml` 的 `token_budget/llm_timeout` 反映 policy 默认值，
实际生效值以 `eval_record.json` 的 `limits` 为准。

## 输入资产

- `--task <dir>`：`task.json` + `data.csv`（或 `data/data.csv`）+ `reference.py`
  （canonical Task 布局，与 ingest `tasks/<T...>/`、suite tasks 一致）。
- `--agent <dir>`：`agent.py`（ingest `agents/<A...>/` 布局）。

## 目录组织

单次（输出目录即 fresh workspace，沿用仓库 `runs/<run_id>/` 证据结构）：

```text
eval_runs/single_001/
├── agent.py / task.json / data.csv / reference.py / .env / .env.example
├── metadata.json / build_manifest.json      # 现有 Build 审计产物
├── runs/<run_id>/result.json + config.yaml + llm_calls.json
├── execution.log                             # 评测层运行日志（脱敏）
└── eval_record.json                          # 评测层元数据（脱敏）
```

批量：

```text
eval_runs/batch_001/
├── batch_manifest.json                       # 组合计划 + 逐条状态（resume 依据）
├── deepseek/T000001__A000001/…               # 每组合一个 fresh workspace
│   └── runs/<run_id>/result.json + execution.log + eval_record.json
├── deepseek/T000001__A000002/…
├── qwen/…
├── summary.csv                               # UTF-8（带 BOM，Excel 可直接打开）
└── summary.json                              # 统计 + 逐组合索引
```

## 结果分类（final_outcome）

映射层唯一实现：`codesetarena.eval.outcome.summarize_outcome`
（只翻译现有 Outcome Model，不做任何新判定）：

| runtime `result.json` | final_outcome | error_category |
|---|---|---|
| completed + correct | `correct` | （空） |
| analysis_failure | `wrong_answer` | （空） |
| internal_error: llm_api | `api_error` | `llm_api` |
| internal_error: token_budget | `token_budget_exceeded` | `token_budget` |
| internal_error: resource_limit(time) | `timeout` | `resource_limit:time` |
| internal_error: resource_limit(memory/disk) | `runtime_error` | `resource_limit:memory` |
| internal_error: runtime_crash | `runtime_error` | `runtime_crash` |
| internal_error: reference_error | `unevaluated` | `reference_error` |
| 组合输入非法（缺 task.json 等） | `unevaluated` | `invalid_input` |

Internal Error 绝不混入 `wrong_answer`；未产生有效评价时 `correct` 列留空。

## summary.csv 字段

`batch_id, run_id, task_id, agent_id, model, execution_status, final_outcome,
correct, elapsed_seconds, tokens_used, token_budget, timeout_seconds,
error_category, error_message, result_path, log_path`

- 一条 Task × Agent × Model 组合对应一行，**包括失败 / 未执行的组合**；
- 计划组合数恒等于行数；`result_path`/`log_path` 只指向真实存在的文件
  （未执行为空）；
- 结果文件缺失 / 不可读 → `execution_status=result_read_error`，绝不静默忽略；
- `summary.json` 统计：`planned_total / executed_total / input_errors /
  result_read_errors / not_executed / counts{correct, wrong_answer, timeout,
  token_budget_exceeded, api_error, runtime_error, unevaluated} / tokens_total /
  per_model / missing`。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 完成（batch：全部组合已执行且全部有有效评价） |
| 1 | 完成但存在未成功 / 未评价组合（wrong_answer 也属完成，计 0） |
| 2 | 配置或输入错误（配置缺失 / JSON 非法 / 缺 API Key / 输出目录冲突 / 组合输入资产非法） |

## 中断与恢复

- 每条组合独立落盘，前一条完成并写盘后才执行下一条（只串行，不并发）；
- 单条失败（超时 / API 错误 / 崩溃 / 输入非法）只记录，批次继续；
- Ctrl-C：保留已完成结果，剩余组合标记 `not_executed`，照常生成部分汇总；
- `--resume`：依据 `batch_manifest.json` 跳过 `executed` 组合继续跑；
- `--force`：删除既有目录重建（显式参数控制，绝不静默覆盖）。
