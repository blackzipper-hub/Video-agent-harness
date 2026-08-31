"""DISABLE_BILLING 开关：为 True 时跳过额度预检查与扣款，且不触碰计费 DB。

默认 False 时保持既有行为（本测试只覆盖新增的短路路径）。
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.utils import credit_deduction_utils as cdu


def _settings(disable: bool):
    return SimpleNamespace(DISABLE_BILLING=disable)


# 代码里是函数内 `from ..config import get_settings`，所以要 patch 源模块的符号
CONFIG_GET_SETTINGS = "app.config.get_settings"


@pytest.mark.asyncio
async def test_check_credits_passes_when_billing_disabled():
    """DISABLE_BILLING=True：预检查直接放行，不访问 AsyncSessionLocal。"""
    with patch(CONFIG_GET_SETTINGS, return_value=_settings(True)):
        with patch.object(
            cdu, "AsyncSessionLocal",
            side_effect=AssertionError("DB should not be touched when billing disabled"),
        ):
            ok, required, reason = await cdu.check_credits_before_task("user-123")
    assert ok is True
    assert required == 0
    assert reason == ""


@pytest.mark.asyncio
async def test_deduct_is_noop_when_billing_disabled():
    """DISABLE_BILLING=True：扣款为 no-op，返回成功且积分=0，不写 credit_history。"""
    with patch(CONFIG_GET_SETTINGS, return_value=_settings(True)):
        with patch.object(
            cdu, "AsyncSessionLocal",
            side_effect=AssertionError("DB should not be touched when billing disabled"),
        ):
            success, credits, cost = await cdu.deduct_credits_with_cost(
                run_id="run-1", user_id="user-123", action="use",
                cost_float=1.23, credits_int=456,
            )
    assert success is True
    assert credits == 0
    assert cost == 1.23
