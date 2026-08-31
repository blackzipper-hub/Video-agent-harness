from app.agents import AgentRole
from app.agents.audio import AUDIO_AGENT
from app.agents.creative import CREATIVE_AGENT
from app.agents.director import DIRECTOR_AGENT
from app.agents.editor import EDITOR_AGENT
from app.agents.quality import QUALITY_AGENT
from app.agents.visual import VISUAL_AGENT


def test_specialist_agents_have_distinct_bounded_roles():
    agents = (
        DIRECTOR_AGENT, CREATIVE_AGENT, VISUAL_AGENT,
        AUDIO_AGENT, EDITOR_AGENT, QUALITY_AGENT,
    )
    assert {agent.role for agent in agents} == {
        AgentRole.DIRECTOR, AgentRole.CREATIVE, AgentRole.VISUAL,
        AgentRole.AUDIO, AgentRole.EDITOR, AgentRole.QUALITY,
    }
    assert VISUAL_AGENT.accepts("atomic.image.generate")
    assert AUDIO_AGENT.accepts("atomic.music.generate")
    assert not AUDIO_AGENT.accepts("atomic.image.generate")


def test_planner_is_the_only_wildcard_orchestration_role():
    from app.agents.planner import PlannerAgent

    assert PlannerAgent.__name__ == "PlannerAgent"
