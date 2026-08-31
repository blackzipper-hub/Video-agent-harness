"""Agent router video vs video_gen routing unit tests (no LLM)."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.chat.services.agent.agent_router_service import (
    AgentRouterService,
    AgentType,
    RouterAnalysisAgentType,
    RouterAnalysisResult,
)
from app.chat.models.user_options import UserOption, VideoGenerationTool


class TestRouterAnalysisAgentTypeSchema:
    def test_accepts_dialog_agent_types(self):
        for value in ("video", "image", "music", "story", "clarify", "chat"):
            result = RouterAnalysisResult(
                agent_type=value,
                confidence=0.9,
                reason="test",
            )
            assert result.agent_type.value == value

    def test_rejects_video_gen_in_analysis_schema(self):
        with pytest.raises(ValidationError):
            RouterAnalysisResult(
                agent_type="video_gen",
                confidence=0.9,
                reason="test",
            )

    def test_router_analysis_enum_excludes_video_gen(self):
        assert "video_gen" not in {item.value for item in RouterAnalysisAgentType}


class TestVideoGenUpgradeHelpers:
    def test_dialog_selected_agent_maps_video_gen_to_video(self):
        assert AgentRouterService._dialog_selected_agent(AgentType.VIDEO_GEN) == AgentType.VIDEO
        assert AgentRouterService._dialog_selected_agent(AgentType.VIDEO) == AgentType.VIDEO

    def test_execution_agent_to_router_analysis_type(self):
        assert (
            AgentRouterService._execution_agent_to_router_analysis_type(AgentType.VIDEO_GEN)
            == RouterAnalysisAgentType.VIDEO
        )
        assert (
            AgentRouterService._execution_agent_to_router_analysis_type(AgentType.IMAGE)
            == RouterAnalysisAgentType.IMAGE
        )


class TestFitsSingleModel:
    def _sd2_option(self, duration: int | None) -> UserOption:
        return UserOption(
            video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V,
            duration=duration,
        )

    def test_short_sd2_fits(self):
        assert AgentRouterService._fits_single_model(self._sd2_option(8)) is True

    def test_long_audio_does_not_fit(self):
        assert AgentRouterService._fits_single_model(self._sd2_option(192)) is False

    def test_sd2_with_short_duration_fits(self):
        option = UserOption(
            video_generation_tool=VideoGenerationTool.SEEDANCE_2_I2V,
            duration=10,
        )
        assert AgentRouterService._fits_single_model(option) is True
