from app.agents.base import AgentRole, SpecialistAgent

AUDIO_AGENT = SpecialistAgent(
    AgentRole.AUDIO,
    "Owns music references, beat analysis, voice and sound design.",
    ("atomic.music.", "music.", "audio.", "sound."),
)
