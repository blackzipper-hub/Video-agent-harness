from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class GraphOperation(StrEnum):
    ADD_TASK = "add_task"
    UPDATE_TASK = "update_task"
    PAUSE_TASK = "pause_task"
    RESUME_TASK = "resume_task"
    CANCEL_TASK = "cancel_task"
    ADD_DEPENDENCY = "add_dependency"
    REMOVE_DEPENDENCY = "remove_dependency"
    REPLACE_INPUT_ARTIFACT = "replace_input_artifact"
    APPLY_WORKFLOW_FRAGMENT = "apply_workflow_fragment"
    REQUEST_APPROVAL = "request_approval"


class TaskGraphPatch(BaseModel):
    operation: GraphOperation
    task_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


def apply_task_graph_patch(tasks: dict[str, dict[str, Any]], patch: TaskGraphPatch) -> dict[str, dict[str, Any]]:
    """Pure task graph transition used by Planner validation and tests."""
    result = {key: dict(value) for key, value in tasks.items()}
    task_id = patch.task_id or str(patch.payload.get("id") or "")
    if patch.operation == GraphOperation.ADD_TASK:
        if not task_id or task_id in result:
            raise ValueError("add_task requires a unique task id")
        result[task_id] = {"id": task_id, "status": "proposed", "depends_on": [], **patch.payload}
    elif patch.operation in {GraphOperation.PAUSE_TASK, GraphOperation.RESUME_TASK, GraphOperation.CANCEL_TASK}:
        if task_id not in result:
            raise ValueError("task not found")
        result[task_id]["status"] = {
            GraphOperation.PAUSE_TASK: "blocked",
            GraphOperation.RESUME_TASK: "proposed",
            GraphOperation.CANCEL_TASK: "cancelled",
        }[patch.operation]
    elif patch.operation == GraphOperation.ADD_DEPENDENCY:
        if task_id not in result or patch.payload.get("depends_on") not in result:
            raise ValueError("dependency task not found")
        dependency = patch.payload["depends_on"]
        if dependency == task_id:
            raise ValueError("task cannot depend on itself")
        result[task_id].setdefault("depends_on", [])
        if dependency not in result[task_id]["depends_on"]:
            result[task_id]["depends_on"].append(dependency)
    elif patch.operation == GraphOperation.REMOVE_DEPENDENCY:
        if task_id not in result:
            raise ValueError("task not found")
        dependency = patch.payload.get("depends_on")
        result[task_id]["depends_on"] = [item for item in result[task_id].get("depends_on", []) if item != dependency]
    elif patch.operation in {GraphOperation.UPDATE_TASK, GraphOperation.REPLACE_INPUT_ARTIFACT}:
        if task_id not in result:
            raise ValueError("task not found")
        result[task_id].update(patch.payload)
    return result
