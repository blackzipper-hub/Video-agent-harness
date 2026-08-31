from app.agents.base import AgentRole, SpecialistAgent

VISUAL_AGENT = SpecialistAgent(
    AgentRole.VISUAL,
    "Owns visual anchors, shot specifications, images and video-shot generation.",
    ("atomic.image.", "atomic.video.", "shot.", "keyframe.", "media."),
)
