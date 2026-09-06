from __future__ import annotations

import hashlib
import json
from typing import Any

from jsonschema import ValidationError as JsonSchemaValidationError
from jsonschema.validators import validator_for

from app.chat.v2.capabilities import CapabilityRegistry
from app.chat.v2.generation_limits import (
    ACTIVE_GENERATION_STATUSES,
    capability_is_video_generation,
    count_active_generation_tasks,
)
from app.chat.v2.models import PlanPatch, RunSnapshot, TaskStatus
from app.chat.v2.workflows import (
    WORKFLOWS,
    active_workflow,
    capability_requires_workflow,
    is_workflow_skill,
)

ACTIVE = {
    TaskStatus.PROPOSED, TaskStatus.BLOCKED, TaskStatus.READY,
    TaskStatus.RUNNING, TaskStatus.WAITING_EXTERNAL,
}
MEDIA_TERMS = {
    "story": ("story", "故事", "剧本"),
    "image": ("image", "poster", "picture", "illustration", "图片", "图像", "海报", "插画"),
    "music": ("music", "song", "soundtrack", "音乐", "歌曲", "配乐"),
    "video": ("video", "film", "movie", "视频", "影片", "短片"),
    "outline": ("outline", "story outline", "大纲", "梗概"),
    "character": ("character", "characters", "角色", "人物"),
    "scene": ("scene", "scenes", "场景"),
    "shot": ("shot", "shots", "storyboard", "分镜", "镜头", "详细分镜"),
    "keyframe": ("keyframe", "keyframes", "关键帧"),
}
# Keep correctly encoded user-facing terms alongside legacy mojibake aliases.
# This makes scope validation deterministic for messages sent by the Chinese UI.
MEDIA_TERMS["story"] += ("故事", "剧本")
MEDIA_TERMS["image"] += ("图片", "图像", "图", "海报", "插画", "角色图", "女主图")
MEDIA_TERMS["music"] += ("音乐", "歌曲", "配乐", "原声", "mv", "music video", "卡点", "原创配乐")
MEDIA_TERMS["video"] += ("视频", "影片", "电影", "短片", "成片", "mv", "music video")
MEDIA_TERMS["outline"] += ("大纲", "梗概")
MEDIA_TERMS["character"] += ("角色", "人物", "女主", "男主")
MEDIA_TERMS["scene"] += ("场景",)
MEDIA_TERMS["shot"] += ("分镜", "镜头", "详细分镜")
MEDIA_TERMS["keyframe"] += ("关键帧",)

# Workflows whose spine is music — allow music.generate even when the latest
# short confirmation message does not restate "配乐/歌曲".
MUSIC_SPINE_WORKFLOWS = frozenset({"mv"})

# Keep these source-safe because this module still contains legacy mojibake.
# Explicit video deliverables must win over the type of an uploaded reference
# image when validating the downstream generation scope.
MEDIA_TERMS["video"] += (
    "mp4",
    "\u89c6\u9891",       # video
    "\u5f71\u7247",       # film
    "\u7535\u5f71",       # movie
    "\u77ed\u7247",       # short film
    "\u6210\u7247",       # finished film
    "\u5e7f\u544a\u7247", # commercial
    "\u5ba3\u4f20\u7247", # promotional film
    "\u4ea7\u54c1\u5ba3\u4f20\u7247", # product promotional film
    "\u5546\u4e1a\u5ba3\u4f20\u7247", # commercial promotional film
    "\u89c6\u9891\u7247\u6bb5", # video segment
    "seedance",
    "\u5373\u68a6",       # Jimeng / Seedance product name
    "\u89c6\u9891\u751f\u6210", # video generation
    "\u89c6\u9891\u6a21\u578b", # video model
    "\u62fc\u63a5",       # concatenate video segments
    "concat",
    # A request to add/burn captions authorizes creation of a derived
    # captioned MP4 from an already selected video. Without these terms the
    # transcript and SRT stages succeed, but media.subtitle_burn is incorrectly
    # rejected merely because its output type is video.
    "字幕",
    "加字幕",
    "增加字幕",
    "烧录字幕",
    "字幕烧录",
    "硬字幕",
    "caption",
    "captions",
    "captioned",
    "subtitle",
    "subtitles",
    "burn-in",
    "burn in",
)


def _media_requested_by_text(text: str) -> set[str]:
    normalized = text.strip().lower()
    return {
        media
        for media, terms in MEDIA_TERMS.items()
        if any(term in normalized for term in terms)
    }
def normalized_parameters(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class PlanValidator:
    def __init__(
        self,
        capabilities: CapabilityRegistry,
        *,
        max_revisions: int,
        max_tasks: int,
        max_parallel_generation_tasks: int = 2,
        skill_catalog=None,
    ):
        self.capabilities = capabilities
        self.max_revisions = max_revisions
        self.max_tasks = max_tasks
        self.max_parallel_generation_tasks = max(1, max_parallel_generation_tasks)
        self.skill_catalog = skill_catalog

    def validate(self, snapshot: RunSnapshot, patch: PlanPatch) -> None:
        if patch.base_revision != snapshot.run.current_revision:
            raise ValueError(
                f"stale plan revision: expected {snapshot.run.current_revision}, got {patch.base_revision}"
            )
        # Count revisions for the current goal only. A new user message resets
        # goal_started_revision so long threads can continue past the absolute
        # revision watermark.
        revisions_used = max(
            0,
            int(snapshot.run.current_revision) - int(snapshot.run.goal_started_revision or 0),
        )
        if revisions_used >= self.max_revisions:
            raise ValueError("maximum plan revisions reached")
        if len(snapshot.tasks) + len(patch.add_tasks) > self.max_tasks:
            raise ValueError("maximum task count reached")
        active = [task for task in snapshot.tasks if task.status in ACTIVE]
        if patch.goal_satisfied and (active or patch.add_tasks):
            raise ValueError("the goal cannot be completed while tasks are active")
        if patch.waiting_for_input and patch.add_tasks:
            raise ValueError("a waiting-for-input patch cannot add tasks")
        if patch.waiting_for_input and patch.interruption is None:
            raise ValueError(
                "waiting_for_input requires a structured interruption explanation"
            )
        if not patch.waiting_for_input and patch.interruption is not None:
            raise ValueError("interruption metadata requires waiting_for_input=true")
        self._validate_interruption(snapshot, patch)
        self._validate_user_scope(snapshot, patch)
        self._validate_workflow_gate(snapshot, patch)
        self._validate_generation_concurrency(snapshot, patch)

        artifacts = {item.id: item for item in snapshot.artifacts}
        signatures = {
            (
                task.capability_id, tuple(sorted(task.input_artifact_version_ids)),
                normalized_parameters(task.parameters),
            )
            for task in snapshot.tasks if task.status in ACTIVE
        }
        keys: set[str] = set()
        workflow = active_workflow(snapshot.run.activated_skills)
        cuti_voice_hashes = {
            value
            for task in snapshot.tasks
            if (value := self._cuti_voice_profile_hash(
                task.capability_id, task.parameters
            ))
        }
        for planned in patch.add_tasks:
            planned.capability_id = self.capabilities.canonical_id(planned.capability_id)
            capability = self.capabilities.get(planned.capability_id)
            if capability.parameters_schema:
                validator = validator_for(capability.parameters_schema)(
                    capability.parameters_schema
                )
                try:
                    validator.validate(planned.parameters)
                except JsonSchemaValidationError as exc:
                    path = ".".join(str(item) for item in exc.absolute_path)
                    location = f" at {path}" if path else ""
                    raise ValueError(
                        f"task {planned.client_key} has invalid parameters{location}: "
                        f"{exc.message}"
                    ) from exc
            if planned.client_key in keys:
                raise ValueError(f"duplicate task client_key: {planned.client_key}")
            keys.add(planned.client_key)
            if any(item not in artifacts for item in planned.input_artifact_version_ids):
                raise ValueError(f"task {planned.client_key} references an unknown artifact")
            input_types = {
                artifacts[item].type for item in planned.input_artifact_version_ids
            }
            missing = set(capability.inputs.required) - input_types
            if missing:
                raise ValueError(f"task {planned.client_key} is missing inputs: {sorted(missing)}")
            allowed = {
                *capability.inputs.required, *capability.inputs.soft,
                *capability.inputs.optional,
            }
            if input_types - allowed:
                raise ValueError(f"task {planned.client_key} has unsupported inputs")
            if workflow and workflow.skill_name == "cuti-product-workflow":
                self._validate_cuti_product_reference_task(
                    planned.capability_id,
                    planned.parameters,
                    [artifacts[item] for item in planned.input_artifact_version_ids],
                )
                voice_hash = self._validate_cuti_product_script_task(
                    planned.capability_id,
                    planned.parameters,
                )
                if voice_hash:
                    cuti_voice_hashes.add(voice_hash)
            if workflow and workflow.skill_name == "cuti-scenario-product-workflow":
                self._validate_scenario_product_reference_task(
                    planned.capability_id,
                    planned.parameters,
                    [artifacts[item] for item in planned.input_artifact_version_ids],
                )
            signature = (
                planned.capability_id, tuple(sorted(planned.input_artifact_version_ids)),
                normalized_parameters(planned.parameters),
            )
            if signature in signatures:
                raise ValueError(f"duplicate active task proposed for {planned.capability_id}")
            signatures.add(signature)

        if len(cuti_voice_hashes) > 1:
            raise ValueError(
                "cuti-product-workflow requires the exact same persisted "
                "voice_profile across every narrated video segment"
            )

        known = {task.id for task in snapshot.tasks} | keys
        for planned in patch.add_tasks:
            unknown = set(planned.depends_on) - known
            if unknown:
                raise ValueError(f"task {planned.client_key} has unknown dependencies: {sorted(unknown)}")
            if planned.client_key in planned.depends_on:
                raise ValueError(f"task {planned.client_key} cannot depend on itself")
        cancellable = {task.id for task in snapshot.tasks if task.status not in {
            TaskStatus.SUCCEEDED, TaskStatus.CANCELLED,
        }}
        if set(patch.cancel_task_ids) - cancellable:
            raise ValueError("one or more tasks cannot be cancelled")
        self._validate_cycles(patch)

    @staticmethod
    def _validate_cuti_product_reference_task(
        capability_id: str,
        parameters: dict[str, Any],
        selected_artifacts: list[Any],
    ) -> None:
        """Make the product setting sheet a durable stage dependency."""
        if capability_id == "atomic.image.generate":
            role = str(parameters.get("artifact_role") or "").strip()
            if role != "product_360_reference":
                raise ValueError(
                    "cuti-product-workflow image generation requires "
                    "artifact_role=product_360_reference"
                )
            model = str(parameters.get("model") or "").strip().lower()
            if model not in {"gpt-image-2", "gpt_image_2"}:
                raise ValueError(
                    "cuti-product-workflow 360-degree setting image requires "
                    "model=gpt-image-2"
                )
            return
        if capability_id not in {"api.provider.generate", "api.ark_protocol.generate"}:
            return
        has_setting_reference = any(
            item.type == "image"
            and str((item.metadata or {}).get("artifact_role") or "").strip()
            == "product_360_reference"
            for item in selected_artifacts
        )
        if not has_setting_reference:
            raise ValueError(
                "cuti-product-workflow video generation requires a selected "
                "product_360_reference image artifact in input_artifact_version_ids"
            )

    @staticmethod
    def _validate_scenario_product_reference_task(
        capability_id: str,
        parameters: dict[str, Any],
        selected_artifacts: list[Any],
    ) -> None:
        """Require script-derived character, scene, and product references."""
        if capability_id == "atomic.image.generate":
            role = str(parameters.get("artifact_role") or "").strip()
            allowed_roles = {
                "character_setting_reference",
                "scene_setting_reference",
                "product_setting_reference",
            }
            if role not in allowed_roles:
                raise ValueError(
                    "cuti-scenario-product-workflow image generation requires "
                    "artifact_role=character_setting_reference, "
                    "scene_setting_reference, or product_setting_reference"
                )
            model = str(parameters.get("model") or "").strip().lower()
            if model not in {"gpt-image-2", "gpt_image_2"}:
                raise ValueError(
                    "cuti-scenario-product-workflow setting image "
                    "requires model=gpt-image-2"
                )
            if not any(item.type == "text" for item in selected_artifacts):
                raise ValueError(
                    "cuti-scenario-product-workflow setting image "
                    "requires the completed script artifact in input_artifact_version_ids"
                )
            return
        if capability_id not in {"api.provider.generate", "api.ark_protocol.generate"}:
            return
        required_roles = {
            "character_setting_reference",
            "scene_setting_reference",
            "product_setting_reference",
        }
        selected_roles = {
            str((item.metadata or {}).get("artifact_role") or "").strip()
            for item in selected_artifacts
            if item.type == "image"
        }
        missing_roles = sorted(required_roles - selected_roles)
        if missing_roles:
            raise ValueError(
                "cuti-scenario-product-workflow video generation is missing setting "
                "references in input_artifact_version_ids: " + ", ".join(missing_roles)
            )
        profile = parameters
        if capability_id == "api.ark_protocol.generate":
            profile = parameters.get("body") if isinstance(parameters.get("body"), dict) else {}
        if not list(profile.get("images") or []):
            raise ValueError(
                "cuti-scenario-product-workflow video generation requires the "
                "uploaded product reference in parameters.images"
            )
        ad_format = str(profile.get("ad_format") or "").strip()
        selling_point = str(profile.get("primary_selling_point") or "").strip()
        segment_proof = str(profile.get("segment_proof") or "").strip()
        prompt = str(profile.get("prompt") or "").strip()
        if ad_format != "product_commercial":
            raise ValueError(
                "cuti-scenario-product-workflow video generation requires "
                "ad_format=product_commercial"
            )
        if not selling_point:
            raise ValueError(
                "cuti-scenario-product-workflow video generation requires a non-empty "
                "primary_selling_point"
            )
        if not segment_proof:
            raise ValueError(
                "cuti-scenario-product-workflow video generation requires a non-empty "
                "segment_proof"
            )
        if not any(term in prompt.casefold() for term in (
            "产品宣传片", "产品广告", "product commercial", "product advertisement",
        )):
            raise ValueError(
                "cuti-scenario-product-workflow final provider prompt must explicitly "
                "identify the task as a product advertisement"
            )
        missing_prompt_values = [
            name for name, value in (
                ("primary_selling_point", selling_point),
                ("segment_proof", segment_proof),
            )
            if value.casefold() not in prompt.casefold()
        ]
        if missing_prompt_values:
            raise ValueError(
                "cuti-scenario-product-workflow final provider prompt lost required "
                "advertising context: " + ", ".join(missing_prompt_values)
            )

    @staticmethod
    def _cuti_generation_profile(
        capability_id: str,
        parameters: dict[str, Any],
    ) -> dict[str, Any] | None:
        if capability_id == "api.provider.generate":
            return parameters
        if capability_id == "api.ark_protocol.generate":
            body = parameters.get("body")
            return body if isinstance(body, dict) else None
        return None

    @classmethod
    def _cuti_voice_profile_hash(
        cls,
        capability_id: str,
        parameters: dict[str, Any],
    ) -> str | None:
        profile = cls._cuti_generation_profile(capability_id, parameters)
        if profile is None:
            return None
        contract = profile.get("_workflow_contract")
        if not isinstance(contract, dict):
            return None
        value = str(contract.get("voice_profile_hash") or "").strip()
        return value or None

    @classmethod
    def _validate_cuti_product_script_task(
        cls,
        capability_id: str,
        parameters: dict[str, Any],
    ) -> str | None:
        """Enforce the shared visual/effect/narration segment contract."""
        profile = cls._cuti_generation_profile(capability_id, parameters)
        if profile is None:
            return None
        contract = profile.get("_workflow_contract")
        if not isinstance(contract, dict):
            raise ValueError(
                "cuti-product-workflow video generation requires "
                "parameters._workflow_contract"
            )
        segment = contract.get("segment_script")
        if not isinstance(segment, dict):
            raise ValueError("cuti product video requires segment_script")
        if int(segment.get("duration_seconds") or 0) != 15:
            raise ValueError("cuti product segment_script duration_seconds must be 15")
        beats = segment.get("beats")
        if not isinstance(beats, list) or not beats:
            raise ValueError("cuti product segment_script requires visual beats")
        previous_end = 0.0
        narrated = []
        for index, beat in enumerate(beats):
            if not isinstance(beat, dict) or not str(beat.get("beat_id") or "").strip():
                raise ValueError("every segment_script beat requires beat_id")
            if not isinstance(beat.get("visual"), dict) or not beat["visual"]:
                raise ValueError("every segment_script beat requires visual direction")
            try:
                start = float(beat.get("start_seconds"))
                end = float(beat.get("end_seconds"))
            except (TypeError, ValueError) as exc:
                raise ValueError("segment_script beat times must be numeric") from exc
            if start != previous_end or end <= start or end > 15:
                raise ValueError(
                    "segment_script beats must continuously cover 0-15 seconds "
                    "without gaps or overlaps"
                )
            previous_end = end
            narration = beat.get("narration")
            if narration not in (None, {}) and not isinstance(narration, dict):
                raise ValueError("beat narration must be an object or null")
            if isinstance(narration, dict) and str(narration.get("text") or "").strip():
                status = str(beat.get("selling_point_status") or "").strip().lower()
                if status not in {"verified", "conceptual"}:
                    raise ValueError(
                        "narrated beats require selling_point_status=verified or conceptual"
                    )
                try:
                    speech_start = float(narration.get("start_seconds"))
                    speech_end = float(narration.get("end_seconds"))
                except (TypeError, ValueError) as exc:
                    raise ValueError("narration times must be numeric") from exc
                if speech_start < start or speech_end > end or speech_end <= speech_start:
                    raise ValueError("narration timing must stay inside its visual beat")
                narrated.append(narration)
        if previous_end != 15.0:
            raise ValueError("segment_script beats must end at 15 seconds")

        mode = str(contract.get("narration_mode") or "").strip().lower()
        if mode not in {"native_voiceover", "music_only"}:
            raise ValueError(
                "cuti product segment requires narration_mode=native_voiceover or music_only"
            )
        if mode == "music_only":
            if narrated:
                raise ValueError("music_only segment cannot contain narration lines")
            return None
        if not narrated:
            raise ValueError("native_voiceover segment requires at least one narration line")
        if profile.get("generate_audio") is not True:
            raise ValueError("native voiceover requires generate_audio=true")
        voice_profile = contract.get("voice_profile")
        if not isinstance(voice_profile, dict) or not voice_profile:
            raise ValueError("native voiceover requires one persisted voice_profile")
        canonical = json.dumps(
            voice_profile,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        expected_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        actual_hash = str(contract.get("voice_profile_hash") or "").strip()
        if actual_hash != expected_hash:
            raise ValueError(
                "voice_profile_hash is missing or does not match the frozen voice_profile"
            )
        return actual_hash

    def _validate_generation_concurrency(
        self,
        snapshot: RunSnapshot,
        patch: PlanPatch,
    ) -> None:
        limit = self.max_parallel_generation_tasks
        active = count_active_generation_tasks(snapshot, self.capabilities)
        cancelling = sum(
            1
            for task in snapshot.tasks
            if task.id in set(patch.cancel_task_ids)
            and task.status in ACTIVE_GENERATION_STATUSES
            and capability_is_video_generation(self.capabilities, task.capability_id)
        )
        adding = 0
        for planned in patch.add_tasks:
            capability_id = self.capabilities.canonical_id(planned.capability_id)
            if capability_is_video_generation(self.capabilities, capability_id):
                adding += 1
        projected = active - cancelling + adding
        if projected > limit:
            slots = max(0, limit - (active - cancelling))
            raise ValueError(
                f"too many concurrent video-generation tasks: "
                f"projected {projected} exceeds limit {limit} "
                f"({slots} slot(s) remaining). "
                f"Submit at most {slots} video generation task(s) now; "
                f"wait for running ones to finish, then propose the next batch. "
                f"Assemble/concat/edit do not count against this limit."
            )

    def _validate_workflow_gate(self, snapshot: RunSnapshot, patch: PlanPatch) -> None:
        if not patch.add_tasks:
            return
        workflow = active_workflow(snapshot.run.activated_skills)
        for planned in patch.add_tasks:
            capability_id = self.capabilities.canonical_id(planned.capability_id)
            if not capability_requires_workflow(capability_id):
                continue
            if workflow is None:
                raise ValueError(
                    "no confirmed workflow skill is activated; "
                    "propose a workflow via waiting_for_input "
                    "(interruption.category=workflow_confirm) and wait for the user "
                    "to confirm with $seedance2 / $mv / $short-drama-workflow "
                    "before scheduling stage tasks"
                )
            if capability_id == "keyframe.generate" and not workflow.requires_keyframe:
                raise ValueError(
                    f"workflow {workflow.skill_name} skips keyframes; "
                    f"do not schedule keyframe.generate"
                )
            if (
                workflow.allowed_capabilities is not None
                and capability_id not in workflow.allowed_capabilities
            ):
                raise ValueError(
                    f"capability {capability_id} is not allowed under workflow "
                    f"{workflow.skill_name}"
                )
            if workflow.skill_name == "libtv-product-workflow":
                self._validate_libtv_multireference_task(capability_id, planned.parameters)

    @staticmethod
    def _validate_libtv_multireference_task(
        capability_id: str,
        parameters: dict[str, Any],
    ) -> None:
        """Keep LibTV on Seedance-2 multireference R2V, never keyframe I2V."""
        forbidden_generation = {
            "atomic.image.generate", "image.generate", "keyframe.generate",
            "atomic.video.generate", "video.generate", "video_gen.generate",
            "shot.video.generate", "video.pipeline.generate",
        }
        if capability_id in forbidden_generation:
            raise ValueError(
                "libtv-product-workflow uses direct Seedance 2 multireference "
                "video generation; storyboard/keyframe and I2V capabilities are forbidden"
            )
        if capability_id not in {"api.provider.generate", "api.ark_protocol.generate"}:
            return
        profile = parameters
        if capability_id == "api.ark_protocol.generate":
            profile = parameters.get("body") or {}
        model = str(profile.get("model") or "").lower()
        if "seedance-2" not in model and "seedance_2" not in model and "seedance2" not in model:
            raise ValueError("libtv product video generation requires a Seedance 2 model")
        mode = str(profile.get("mode") or profile.get("generation_mode") or "").lower()
        if mode != "reference_to_video":
            raise ValueError(
                "libtv product video generation requires mode=reference_to_video"
            )
        forbidden_keys = {
            "start_frame", "first_frame", "last_frame", "start_image",
            "start_image_url", "end_image", "end_image_url",
            "start_frame_artifact_version_id",
        }
        present = sorted(key for key in forbidden_keys if profile.get(key) not in (None, "", []))
        if present:
            raise ValueError(
                f"libtv multireference video task contains forbidden frame binding: {present}"
            )
        roles = profile.get("reference_roles")
        images = profile.get("images") or profile.get("reference_images") or []
        if images and (not isinstance(roles, list) or len(roles) != len(images)):
            raise ValueError(
                "libtv multireference images require an equally-sized reference_roles list"
            )

    def _validate_interruption(self, snapshot: RunSnapshot, patch: PlanPatch) -> None:
        interruption = patch.interruption
        if interruption is None:
            return
        if interruption.category == "workflow_confirm":
            if not interruption.requires_confirmation:
                raise ValueError(
                    "workflow_confirm interruption must require user confirmation"
                )
            if not interruption.skill_name or not is_workflow_skill(interruption.skill_name):
                raise ValueError(
                    "workflow_confirm must name an installed workflow skill "
                    f"(one of: {sorted(WORKFLOWS)})"
                )
            if self.skill_catalog is not None and not self.skill_catalog.has(
                interruption.skill_name
            ):
                raise ValueError(
                    f"workflow skill is not installed: {interruption.skill_name}"
                )
            return
        if interruption.category != "skill_required":
            return
        if not interruption.requires_confirmation:
            raise ValueError(
                "skill_required interruption must require user confirmation"
            )
        if not all((
            interruption.skill_name,
            interruption.skill_resource,
            interruption.skill_policy,
        )):
            raise ValueError(
                "skill_required interruption must identify skill_name, "
                "skill_resource, and the exact skill_policy"
            )
        if interruption.skill_name not in snapshot.run.activated_skills:
            raise ValueError(
                f"interrupting skill is not activated for this run: "
                f"{interruption.skill_name}"
            )
        if self.skill_catalog is None:
            return
        if not self.skill_catalog.has(interruption.skill_name):
            raise ValueError(
                f"interrupting skill is not installed: {interruption.skill_name}"
            )
        skill = self.skill_catalog.load(interruption.skill_name)
        resource = interruption.skill_resource.replace("\\", "/")
        if resource == "SKILL.md":
            source = skill.instructions
        else:
            if resource not in self.skill_catalog.list_resources(interruption.skill_name):
                raise ValueError(
                    f"interrupting skill resource does not exist: {resource}"
                )
            source = self.skill_catalog.read_resource(
                interruption.skill_name, resource,
            )
        policy = " ".join(interruption.skill_policy.lower().split())
        normalized_source = " ".join(source.lower().split())
        if policy not in normalized_source:
            raise ValueError(
                "skill_policy must quote an exact policy from the identified resource"
            )

    def _validate_user_scope(self, snapshot: RunSnapshot, patch: PlanPatch) -> None:
        latest_user_message = next(
            (item.content for item in reversed(snapshot.messages) if item.role == "user"),
            snapshot.run.objective,
        )
        normalized = latest_user_message.strip().lower()
        continuation_markers = (
            "继续", "继续生成", "恢复", "确认", "同意", "批准", "下一步",
            "通过", "重新生成", "再次生成", "重试", "再试", "拼接", "组装",
            "continue", "approved", "approve", "confirmed", "go ahead", "next",
            "retry", "regenerate", "try again", "concat", "assemble",
        )
        # Continuations like "确认生成原创配乐" still carry media terms — union
        # them with the original goal instead of discarding the latest message.
        requested_media = _media_requested_by_text(normalized)
        if (
            len(normalized) <= 120
            and any(marker in normalized for marker in continuation_markers)
        ):
            original_goal = next(
                (
                    item.content for item in snapshot.messages
                    if item.role == "user" and item.content.strip()
                ),
                snapshot.run.objective,
            )
            requested_media |= _media_requested_by_text(original_goal)
            requested_media |= _media_requested_by_text(snapshot.run.objective or "")
        workflow = active_workflow(snapshot.run.activated_skills)
        if workflow is not None and workflow.skill_name in MUSIC_SPINE_WORKFLOWS:
            requested_media.add("music")
            requested_media.add("video")
        # An explicitly activated workflow is part of the request. Its declared
        # video-producing pipeline unlocks normal video dependencies even when
        # the brief names a format (for example "short drama") instead of video.
        if workflow is not None:
            for capability_id in workflow.pipeline:
                try:
                    capability = self.capabilities.get(
                        self.capabilities.canonical_id(capability_id)
                    )
                except (LookupError, ValueError):
                    continue
                if capability.output_type == "video":
                    requested_media.add("video")
                    break
        proposed_media = set()
        for item in patch.add_tasks:
            try:
                capability = self.capabilities.get(
                    self.capabilities.canonical_id(item.capability_id)
                )
            except (LookupError, ValueError):
                continue
            media = capability.output_type
            if media in MEDIA_TERMS:
                proposed_media.add(media)
        if "video" in requested_media:
            # Text/image planning artifacts are legitimate dependencies of a
            # composed video. Music remains opt-in unless the request/workflow
            # explicitly asks for it (MV / 配乐).
            proposed_media -= {
                "story", "image", "outline", "character",
                "scene", "shot", "keyframe",
            }
        if proposed_media - requested_media:
            unexpected = sorted(proposed_media - requested_media)
            raise ValueError(
                f"proposed media exceeds the latest user request scope: {unexpected}"
            )

    @staticmethod
    def _validate_cycles(patch: PlanPatch) -> None:
        keys = {item.client_key for item in patch.add_tasks}
        graph = {
            item.client_key: [value for value in item.depends_on if value in keys]
            for item in patch.add_tasks
        }
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError("plan patch contains a dependency cycle")
            if node in visited:
                return
            visiting.add(node)
            for dependency in graph[node]:
                visit(dependency)
            visiting.remove(node)
            visited.add(node)

        for key in graph:
            visit(key)
