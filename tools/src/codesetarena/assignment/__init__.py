"""Assignment Pipeline（生产）：canonical Task -> 匿名 Reviewer Assignment。

四类身份严格分离（本包核心原则）：

- Submission Identity：student_id / submission_id（只存在于私有 Registry）
- Canonical Identity：task_id(T######) / agent_id(A######)（canonical 对象无 student_id）
- Anonymous Identity：anonymous_task_id(AT###) / anonymous_agent_id(AA###)（单向 opaque）
- Reviewer Identity：reviewer_student_id ↔ reviewer_package_id(R######)（私有映射）

Assignment Pipeline 只读取 ingest 完成的 Canonical Task Repository / Private
Registry；分配算法规格复用 Benchmark 验证过的统一 Solver Core
（``codesetarena.assignment.solver``，唯一实现，Benchmark 亦从其导入）。
"""

from codesetarena.assignment.contract import (  # noqa: F401
    ASSIGNMENT_CONTRACT_ID,
    RunStatus,
    assignment_error,
)
from codesetarena.assignment.category import (  # noqa: F401
    CATEGORY_CODES,
    CATEGORY_INDEX_BY_CODE,
    category_index_of,
    category_code_of,
    normalize_category_code,
)

__all__ = [
    "ASSIGNMENT_CONTRACT_ID",
    "RunStatus",
    "assignment_error",
    "CATEGORY_CODES",
    "CATEGORY_INDEX_BY_CODE",
    "category_index_of",
    "category_code_of",
    "normalize_category_code",
]