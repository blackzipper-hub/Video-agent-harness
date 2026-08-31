from app.agents.base import AgentRole, SpecialistAgent

CREATIVE_AGENT = SpecialistAgent(
    AgentRole.CREATIVE,
    "Creates and maintains story, characters, scenes and the Creative Bible.",
    ("atomic.text.", "story.", "outline.", "character.", "scene."),
)
