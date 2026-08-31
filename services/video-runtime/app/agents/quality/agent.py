from app.agents.base import AgentRole, SpecialistAgent

QUALITY_AGENT = SpecialistAgent(
    AgentRole.QUALITY,
    "Evaluates continuity, safety, technical quality and approval readiness.",
    ("evaluation.", "quality.", "continuity."),
)
