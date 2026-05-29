"""τ-bench adapter PoC 脚本

Task ID   : T-0-T2-TAU
关联 FR   : FR-G02, AC2-1, FR-E01, W5
PoC 假设  : PoC-H2（tau-bench airline task 数 >= 15）
实测维度  :
  - len(tau_bench.envs.airline.tasks.tasks) 验证 task 数量
  - print(vars(tasks.tasks[0])) 确认 actions 字段名（W5）
  - 记录第 1 个 task 的字段结构
期望输出  : JSON 结果到 stdout，含 task_count + task_fields + actions_field_found

运行方式  ::

    cd <project_root>
    python .specify/features/103d-octobench/poc/poc_tau.py

注意：需要 tau-bench 已安装。
安装：uv add git+https://github.com/sierra-research/tau-bench.git

"""
from __future__ import annotations

import argparse
import inspect
import json
import logging
import sys
import time
from typing import Any

logger = logging.getLogger("poc_tau")
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _try_import_tau() -> tuple[Any, str | None]:
    """懒导入 tau_bench airline tasks，失败返回 (None, error_message)。"""
    try:
        from tau_bench.envs.airline import tasks as airline_tasks  # type: ignore

        return airline_tasks, None
    except ImportError as e:
        return None, str(e)
    except Exception as e:
        return None, f"意外错误: {e}"


def probe_task_fields(task: Any) -> dict:
    """探测 task 对象的字段结构（W5：实测确认 actions 字段名）。"""
    fields: dict[str, str] = {}
    # 方法 1：vars()
    try:
        fields.update({k: type(v).__name__ for k, v in vars(task).items()})
    except TypeError:
        pass
    # 方法 2：inspect.getmembers（兜底）
    if not fields:
        for name, val in inspect.getmembers(task):
            if not name.startswith("_") and not callable(val):
                fields[name] = type(val).__name__
    return fields


def main() -> None:
    parser = argparse.ArgumentParser(description="PoC T-0-T2-TAU: tau-bench airline 验证")
    parser.add_argument("--task-idx", type=int, default=0, help="验证的 task 索引（默认 0）")
    args = parser.parse_args()

    t_start = time.perf_counter()
    airline_tasks, err = _try_import_tau()
    import_ms = (time.perf_counter() - t_start) * 1000

    if airline_tasks is None:
        result = {
            "status": "IMPORT_ERROR",
            "error": err,
            "poc_h2": "FAIL",
            "hint": "安装 tau-bench: uv add git+https://github.com/sierra-research/tau-bench.git",
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    # PoC-H2 验证
    task_count = len(airline_tasks.tasks)
    poc_h2_pass = task_count >= 15
    logger.info("tau-bench airline task 数: %d（PoC-H2 %s）", task_count, "PASS" if poc_h2_pass else "FAIL")

    if args.task_idx >= task_count:
        result = {
            "status": "INDEX_OUT_OF_RANGE",
            "task_count": task_count,
            "requested_idx": args.task_idx,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        sys.exit(1)

    task = airline_tasks.tasks[args.task_idx]
    task_fields = probe_task_fields(task)

    # W5：确认 actions 字段名
    # 候选字段名：actions / expected_actions / expected_outputs / ground_truth_actions
    candidate_actions_fields = ["actions", "expected_actions", "expected_outputs", "ground_truth_actions"]
    found_actions_field: str | None = None
    actions_length: int | None = None

    for field_name in candidate_actions_fields:
        val = None
        if hasattr(task, field_name):
            val = getattr(task, field_name)
        elif isinstance(task, dict) and field_name in task:
            val = task[field_name]
        if val is not None:
            found_actions_field = field_name
            if hasattr(val, "__len__"):
                actions_length = len(val)
            break

    # 安全序列化（不依赖 dataclass __dict__ 的可 JSON 化假设）
    task_fields_safe = {}
    for k, v in task_fields.items():
        try:
            json.dumps(v)
            task_fields_safe[k] = v
        except (TypeError, ValueError):
            task_fields_safe[k] = f"<{type(v).__name__}>"

    # 尝试读取 instruction / user_id 字段（τ-bench 常见字段）
    instruction = getattr(task, "instruction", None) or (task.get("instruction") if isinstance(task, dict) else None)
    user_id = getattr(task, "user_id", None) or (task.get("user_id") if isinstance(task, dict) else None)

    total_ms = (time.perf_counter() - t_start) * 1000

    result = {
        "status": "PASS" if poc_h2_pass else "FAIL_POC_H2",
        "task": "T-0-T2-TAU",
        "import_ms": round(import_ms, 1),
        "total_ms": round(total_ms, 1),
        "poc_h2_task_count_gte_15": poc_h2_pass,
        "airline_task_count": task_count,
        "task_idx_probed": args.task_idx,
        "task_fields": task_fields_safe,
        "w5_actions_field_found": found_actions_field,
        "w5_actions_length": actions_length,
        "w5_note": (
            f"W5 确认：actions 字段名 = '{found_actions_field}'"
            if found_actions_field
            else "W5 警告：未找到 actions / expected_actions / expected_outputs 字段——需人工确认 τ-bench API"
        ),
        "sample_instruction": str(instruction)[:200] if instruction else None,
        "sample_user_id": str(user_id) if user_id else None,
        "poc_h4_note": (
            "[TBD-MANUAL-RUN] PoC-H4（mock DB reset 无污染）需实际跑两个连续 task 才能验证；"
            "本 PoC 仅验证 task 数量和字段结构。"
        ),
    }

    if not poc_h2_pass:
        result["fallback_hint"] = (
            f"task 数 = {task_count} < 15，建议检查 retail domain：\n"
            "from tau_bench.envs.retail import tasks as retail_tasks\n"
            "print(len(retail_tasks.tasks))"
        )

    print(json.dumps(result, ensure_ascii=False, indent=2))
    sys.exit(0 if poc_h2_pass else 1)


if __name__ == "__main__":
    main()
