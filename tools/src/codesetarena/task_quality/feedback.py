"""学生反馈生成（§21/§43）：中文一段话、结论性、不暴露隐藏策略/模型推理。"""
from __future__ import annotations


def build_student_feedback(
    *,
    status: str,
    category: str,
    declared_answer: str,
    student_reference_result: str,
    independent_result: str,
    detail: str,
) -> str:
    """确定性模板（不调 LLM，保证可复现且零额外 Token）。1~4 句中文。"""

    if status == "PASS":
        return ("题目含义明确，独立参考程序在你的同一份 data.csv 上得到与 reference.py "
                f"一致的结果（{student_reference_result}），本任务通过质量门禁，可进入下一阶段。")

    if category == "REFERENCE_ANSWER_MISMATCH":
        return ("reference.py 的执行结果（"
                f"{student_reference_result}）与提交的标准答案（{declared_answer}）不一致，"
                "这是确定性错误；请先修正 reference.py 与标准答案使其一致后重新提交。")

    if category == "REFERENCE_CRASH":
        return (f"reference.py 在课程环境中执行失败（{detail}）；标准答案无法产生，"
                "请修复 reference.py 的运行错误后重新提交。")

    if category == "INDEPENDENT_DISAGREEMENT":
        return ("题目本身含义明确，但独立参考程序在同一份 data.csv 上得到 "
                f"{independent_result}，当前 reference.py 与标准答案均得到 "
                f"{student_reference_result}；两份独立实现逻辑冲突，判定为 "
                f"{detail}。请对照题面逐条核对过滤、聚合与边界条件，"
                "修改 reference.py 和标准答案后重新提交；当前任务暂不能进入下一阶段。")

    if category == "AMBIGUOUS":
        return ("独立求解程序认为题目存在歧义，无法唯一确定计算口径"
                f"（{detail}）。请把过滤条件、聚合口径、排序与并列处理、"
                "缺失值处理写明确后重新提交；当前任务进入人工复核。")

    if category == "INSUFFICIENT_DATA":
        return (f"独立求解程序认为当前 data.csv 不足以支撑题目要求（{detail}）。"
                "请补充或调整数据使题目可唯一作答后重新提交；当前任务进入人工复核。")

    if category == "GATE_INFRASTRUCTURE_FAILURE":
        return (f"质量门禁基础设施本次运行异常（{detail}），该结果不计入你的任务质量"
                "判定，也不会影响成绩；请稍后重试或联系教师。")

    return (f"质量门禁未能给出确定结论（{detail}），任务已转人工复核；"
            "请等待教师反馈。")
