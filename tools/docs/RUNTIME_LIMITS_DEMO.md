# Runtime Resource Limits — Teacher Demo

## 目的

现场直观演示 Runtime 资源监管的四种结果（全部由真实 Runtime 执行产生，无任何伪造、不调用 LLM、不消耗 Token）：

| 案例 | Agent | Timeout | 预期 |
|---|---|---|---|
| Demo 01 `too-loose` | 坏 Agent（死循环） | 10s | TIMEOUT（长期占用被终止） |
| Demo 02 `too-strict` | 好 Agent（需 ≈3s） | 2s | TIMEOUT（正常 Agent 被误杀） |
| Demo 03 `normal` | 好 Agent（同 02） | 10s | PASS，answer=60 |
| Demo 04 `timeout-isolation` | 坏 Agent（同 01） | 3s | TIMEOUT 后健康检查 PASS（隔离性） |

## 一键命令

```bash
scripts/demo_runtime_limits.sh
```

（Windows：`scripts\demo_runtime_limits.bat`）约 20–30 秒完成；自动 `docker compose up -d`，四案例复用同一容器。

## 单独命令

```bash
bin/datac demo runtime --case too-loose   --workspace /workspace
bin/datac demo runtime --case too-strict  --workspace /workspace
bin/datac demo runtime --case normal      --workspace /workspace
bin/datac demo runtime --case timeout-isolation --workspace /workspace
bin/datac demo runtime --all              --workspace /workspace   # 等价一键脚本
```

## 实现边界

- 全部写入 `workspace/demos/runtime_limits/`（案例文件 + `runs/demo_XX/` 证据），**绝不触碰 `phase1/`、`phase2/`**；
- 每案例 timeout 通过**案例级 Budget** 注入真实 Runtime 内核（`run_with_budget`，与正式 run 同一条 Supervisor 执行链），**不读/不改正式 `policy/runtime_policy.json`**；
- 正确性判定使用真实 `reference.py`（value 列求和 = 60）。

## 预期结果

```
Demo 01 TIMEOUT @~10s   Demo 02 TIMEOUT @~2s
Demo 03 PASS @~3-6s answer=60 correct=true
Demo 04 TIMEOUT @~3s → Runtime health check PASS
phase1/phase2/正式 Policy 均未被修改
```

详细证据：`workspace/demos/runtime_limits/runs/demo_XX/result.json`、`demo_summary.json`。

## 正式 120 秒与演示 timeout 的区别

Demo 中的 **2 / 3 / 10 秒仅用于现场快速复现**，不代表正式课程 Runtime Policy（正式 `wall_clock_seconds=90` + 冷启动垫）。现场讲解口径：同样的问题在正式 120 秒预算下会被放大 12–40 倍占用。
