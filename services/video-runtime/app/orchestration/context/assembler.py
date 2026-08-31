from __future__ import annotations

from app.chat.v2.models import RunSnapshot
from app.chat.v2.language import active_language_skill, output_language_instruction


class ContextAssembler:
    """Builds bounded model context while preserving every raw message in storage."""

    def __init__(
        self,
        recent_message_limit: int = 30,
        *,
        max_parallel_generation_tasks: int = 2,
    ):
        self.recent_message_limit = max(1, recent_message_limit)
        self.max_parallel_generation_tasks = max(1, max_parallel_generation_tasks)

    def assemble(self, snapshot: RunSnapshot) -> str:
        selected_ids = {item.artifact_version_id for item in snapshot.selections}
        selected = [item for item in snapshot.artifacts if item.id in selected_ids]
        messages = snapshot.messages[-self.recent_message_limit:]
        limit = self.max_parallel_generation_tasks
        sections = [
            output_language_instruction(
                snapshot.run.output_language,
                user_request=snapshot.run.objective,
                language_skill=active_language_skill(snapshot.run.activated_skills),
            ),
            f"Durable project summary:\n{snapshot.run.durable_summary or '(none yet)'}",
            (
                "Platform policy (standing):\n"
                f"- At most {limit} concurrent video-generation tasks may be active "
                f"(PROPOSED/READY/RUNNING/WAITING_EXTERNAL) in this run.\n"
                f"- In each propose_plan_patch, add at most the remaining slots "
                f"(never more than {limit} video gens at once).\n"
                "- For multi-segment films (e.g. 12×15s), submit batches of ≤"
                f"{limit}, wait for success, then propose the next batch.\n"
                "- video.assemble / media.concat / media.extract_frame / video.edit "
                "do not count against "
                "this limit. The Harness rejects over-limit patches."
            ),
            (
                f"Project thread_id (use as parameters.thread_id for every stage skill):\n"
                f"{snapshot.run.thread_id}"
            ),
            "Selected artifacts:\n" + "\n".join(
                f"- {item.type} {item.id}: {item.title}; {item.summary}; uri={item.uri or 'none'}"
                for item in selected
            ),
            f"User options:\n{snapshot.run.user_option or {}}",
            "User files:\n" + "\n".join(
                f"- {item.type}: {item.url} ({item.filename or 'unnamed'})"
                for item in snapshot.run.input_files
            ),
            "Recent source messages:\n" + "\n".join(
                f"[{item.sequence}] {item.role}: {item.content}" for item in messages
            ),
        ]
        return "\n\n".join(sections)

    @staticmethod
    def update_durable_summary(snapshot: RunSnapshot) -> tuple[str, int]:
        """Build a bounded source index while raw messages remain independently persisted."""
        if not snapshot.messages:
            return snapshot.run.durable_summary, snapshot.run.summary_through_sequence

        # Keep the original project-defining turns and the latest changes. This avoids
        # the previous tail truncation behavior that could silently discard the user's
        # earliest style, character, or timeline constraints.
        foundation = snapshot.messages[:12]
        recent = snapshot.messages[-80:]
        selected = {item.id: item for item in [*foundation, *recent]}
        ordered = sorted(selected.values(), key=lambda item: item.sequence)
        lines = [
            f"[{item.sequence}] {item.role}: {item.content[:1200]}"
            for item in ordered
        ]
        summary = "\n".join(lines)
        if len(selected) < len(snapshot.messages):
            summary += (
                "\n[Some intermediate messages are omitted from model context; "
                "their complete source text remains in durable chat history.]"
            )
        return summary[:24000], snapshot.messages[-1].sequence
