"""Keep the V2 import surface stable while orchestration owns the implementation."""

from app.chat.v2.context import ContextAssembler as LegacyContextAssembler
from app.chat.v2.harness import DynamicHarness as LegacyHarness
from app.chat.v2.plan_validator import PlanValidator as LegacyPlanValidator
from app.orchestration.context import ContextAssembler
from app.orchestration.policy import PlanValidator
from app.orchestration.task_runtime import DynamicHarness


def test_v2_imports_are_compatibility_shims_for_orchestration():
    assert LegacyContextAssembler is ContextAssembler
    assert LegacyPlanValidator is PlanValidator
    assert LegacyHarness is DynamicHarness
