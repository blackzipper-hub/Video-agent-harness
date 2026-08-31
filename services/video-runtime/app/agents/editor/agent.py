from app.agents.base import AgentRole, SpecialistAgent

EDITOR_AGENT = SpecialistAgent(
    AgentRole.EDITOR,
    "Owns timeline assembly, edits, rendering and delivery versions.",
    ("timeline.", "render.", "video.assemble", "video.edit", "media.concat"),
)
