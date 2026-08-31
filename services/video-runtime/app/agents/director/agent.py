from app.agents.base import AgentRole, SpecialistAgent

DIRECTOR_AGENT = SpecialistAgent(
    AgentRole.DIRECTOR,
    "Own creative intent, workflow selection and approvals; never calls providers directly.",
    ("actions.", "project.", "workflow."),
)
