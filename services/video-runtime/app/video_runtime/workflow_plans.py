"""Deterministic compilers for the distinct Cuti Workflow Skill families."""

from __future__ import annotations

from typing import Any

from app.orchestration.workflow_compiler.registry import WorkflowSpec

from .initial_build import BuildPlanValidationError, SeedanceStoryWorkflow, topological_steps
from .models import MediaArtifactVersion, RebuildPlan, RebuildPlanItem, VideoSpec
from .plugins import PluginContext


UNAVAILABLE_WORKFLOW_MODES = {
    "open_montage": "OpenMontage Bridge is not installed in the Video Runtime",
    "ink_press_product_workflow": "Video Shotcraft/Remotion template runtime is not installed",
}

UNAVAILABLE_WORKFLOW_CAPABILITIES = {
    "open_montage": ["open_montage.tool.invoke"],
    "ink_press_product_workflow": ["video-shotcraft.render"],
}

SUPPORTED_WORKFLOW_MODES = {
    "keyframe_pipeline", "direct_video", "short_drama", "seedance2",
    "mv", "seedance_mv", "short_drama_workflow", "product_ad_video",
    "cuti_product_workflow", "cuti_scenario_product_workflow",
    "libtv_product_workflow",
}


class WorkflowPlanBuilder:
    def __init__(self, workflow: WorkflowSpec, context: PluginContext, spec: VideoSpec) -> None:
        if not context.project_id:
            raise BuildPlanValidationError("workflow requires a project")
        self.workflow = workflow
        self.project_id = context.project_id
        self.base_version_id = str(context.values.get("base_project_version_id") or "")
        self.spec = spec
        self.items: list[RebuildPlanItem] = []
        self.sources: dict[str, MediaArtifactVersion] = dict(
            context.values.get("source_artifacts") or {},
        )
        self.source_steps: dict[str, str] = {}

    def add(
        self,
        step_id: str,
        artifact_type: str,
        capability: str,
        *,
        parameters: dict[str, Any] | None = None,
        depends_on: list[str] | None = None,
        cost: float = 0.0,
        skills: list[str] | None = None,
        action: str = "create",
        artifact_version_id: str = "",
    ) -> str:
        merged = dict(parameters or {})
        if self.spec.workflow_parameters:
            merged.setdefault("workflow_parameters", dict(self.spec.workflow_parameters))
        if capability.startswith(("atomic.", "api.", "suno.")):
            for key, value in self.workflow.parameters.items():
                merged.setdefault(key, value)
            merged.setdefault("activated_workflow", self.workflow.skill_name)
        self.items.append(RebuildPlanItem(
            step_id=step_id,
            artifact_version_id=artifact_version_id,
            output_artifact_id=f"{self.project_id}:{step_id}",
            output_artifact_type=artifact_type,
            action=action,
            capability=capability,
            parameters=merged,
            depends_on=list(dict.fromkeys(depends_on or [])),
            estimated_cost=cost,
            order=len(self.items) + 1,
            reason=f"Workflow Skill: {self.workflow.title}",
            skill_ids=skills or [],
        ))
        return step_id

    def add_sources(self) -> dict[str, str]:
        logical_ids = list(self.spec.source_asset_ids)
        for shot in self.spec.shots:
            logical_ids.extend(shot.reference_asset_ids)
        for index, logical_id in enumerate(dict.fromkeys(logical_ids), start=1):
            artifact = self.sources.get(logical_id)
            if artifact is None:
                raise BuildPlanValidationError(f"source artifact is unavailable: {logical_id}")
            step_id = self.add(
                f"source-{index}", artifact.type, "",
                action="reuse", artifact_version_id=artifact.id,
            )
            self.source_steps[logical_id] = step_id
        return self.source_steps

    def documents(self) -> tuple[str, str, str, str]:
        spec_step = self.add("spec", "video_spec", "runtime.artifact.persist", parameters={
            "title": self.spec.title,
            "content": self.spec.model_dump(mode="json"),
        })
        script = self.add("script", "script", "runtime.artifact.persist", parameters={
            "title": f"{self.spec.title} script",
            "content": [{
                "shotId": shot.id, "beat": shot.beat, "visualPrompt": shot.visual_prompt,
                "narration": shot.narration,
            } for shot in self.spec.shots],
        }, depends_on=[spec_step])
        characters = self.add("characters", "characters", "runtime.artifact.persist", parameters={
            "title": "Characters",
            "content": [item.model_dump(mode="json") for item in self.spec.characters],
        }, depends_on=[spec_step])
        storyboard = self.add("storyboard", "storyboard", "runtime.artifact.persist", parameters={
            "title": "Storyboard",
            "content": [item.model_dump(mode="json") for item in self.spec.shots],
        }, depends_on=[script, characters])
        return spec_step, script, characters, storyboard

    def character_references(
        self,
        manifest_step: str,
        *,
        skills: list[str] | None = None,
        reference_from_steps: list[str] | None = None,
    ) -> dict[str, str]:
        if skills is None:
            skills = ["character-director", "character-image-tool-director"]
        identity_refs = list(dict.fromkeys(reference_from_steps or []))
        result: dict[str, str] = {}
        for character in self.spec.characters:
            parameters: dict[str, Any] = {
                "prompt": (
                    f"Reusable character identity sheet for {character.name}. "
                    f"Appearance: {character.appearance}; clothing: {character.clothing}; "
                    f"personality: {character.personality}"
                ),
                "model": self.spec.providers.image,
                "aspect_ratio": self.spec.aspect_ratio,
                "character_id": character.id,
            }
            if identity_refs:
                parameters["reference_from_steps"] = identity_refs
            result[character.id] = self.add(
                f"character-{character.id}-reference", "character_reference",
                "atomic.image.generate",
                parameters=parameters,
                depends_on=[manifest_step, *identity_refs], cost=0.08,
                skills=skills,
            )
        return result

    def source_refs_for_shot(self, shot) -> list[str]:
        ids = shot.reference_asset_ids or self.spec.source_asset_ids
        return [self.source_steps[item] for item in ids if item in self.source_steps]

    def finish(
        self, clips: list[str], *, transition: float, require_audio: bool,
        audio_step: str = "", captions: bool = False,
    ) -> RebuildPlan:
        timeline = self.add(
            "timeline", "timeline", "media.timeline.compose",
            parameters={
                "shots": [shot.model_dump(mode="json") for shot in self.spec.shots],
                "video_steps": clips,
            }, depends_on=clips,
        )
        if audio_step:
            assembled = self.add(
                "assembled-video", "video_assembled", "media.concat",
                parameters={
                    "video_steps": clips, "normalize": True,
                    "transition_duration": transition,
                }, depends_on=clips, cost=0.02,
            )
            mix_id = "mixed-video" if captions else "final-video"
            mixed = self.add(
                mix_id, "video_mixed" if captions else "final_video", "media.mix_audio",
                parameters={
                    "video_step": assembled, "audio_step": audio_step, "mode": "replace",
                    "audio_volume": 1.0, "final_output": not captions,
                    "require_audio": require_audio,
                    "target_duration_seconds": self.spec.target_duration_seconds,
                }, depends_on=[assembled, audio_step], cost=0.01,
            )
            final_video = mixed
            if captions:
                transcription = self.add(
                    "transcription", "transcription", "media.transcribe",
                    parameters={"video_step": mixed, "language": self.spec.language},
                    depends_on=[mixed],
                )
                caption = {
                    "video_step": mixed, "transcription_step": transcription,
                    "final_output": True, "require_audio": True,
                    "require_subtitles": True,
                    "target_duration_seconds": self.spec.target_duration_seconds,
                }
                wp = self.spec.workflow_parameters or {}
                if wp.get("caption_html"):
                    caption["caption_html"] = wp["caption_html"]
                if wp.get("composition_html"):
                    caption["composition_html"] = wp["composition_html"]
                final_video = self.add(
                    "final-video", "final_video", "media.hyperframes_caption",
                    parameters=caption, depends_on=[mixed, transcription], cost=0.02,
                )
        else:
            final_video = self.add(
                "final-video", "final_video", "media.concat",
                parameters={
                    "video_steps": clips, "normalize": True,
                    "transition_duration": transition, "final_output": True,
                    "require_audio": require_audio, "require_subtitles": False,
                    "target_duration_seconds": self.spec.target_duration_seconds,
                }, depends_on=clips, cost=0.02,
            )
        self.add(
            "validate-final", "validation", "cuti.continuity.validate",
            action="validate", parameters={"timeline_step": timeline, "video_step": final_video},
            depends_on=[*clips, timeline, final_video],
        )
        topological_steps(self.items)
        return RebuildPlan(
            project_id=self.project_id,
            kind="initial",
            base_project_version_id=self.base_version_id,
            workflow_id=self.workflow.skill_name,
            video_spec=self.spec,
            items=self.items,
            estimated_cost=round(sum(item.estimated_cost for item in self.items), 6),
        )


def compile_keyframe(workflow: WorkflowSpec, context: PluginContext, spec: VideoSpec) -> RebuildPlan:
    """The classic keyframe workflow intentionally shares the keyframe compiler."""
    base = spec.model_copy(update={"workflow_id": SeedanceStoryWorkflow.id})
    plan = SeedanceStoryWorkflow().compile(
        project_id=str(context.project_id),
        base_project_version_id=str(context.values.get("base_project_version_id") or ""),
        spec=base,
    )
    for item in plan.items:
        if item.capability.startswith("atomic."):
            item.parameters.update({
                key: item.parameters.get(key, value)
                for key, value in workflow.parameters.items()
            })
            item.parameters["activated_workflow"] = workflow.skill_name
            if spec.workflow_parameters:
                item.parameters.setdefault(
                    "workflow_parameters", dict(spec.workflow_parameters),
                )
        item.reason = f"Workflow Skill: {workflow.title}"
    plan.workflow_id = workflow.skill_name
    plan.video_spec = spec
    return plan


def compile_direct(workflow: WorkflowSpec, context: PluginContext, spec: VideoSpec) -> RebuildPlan:
    b = WorkflowPlanBuilder(workflow, context, spec)
    b.add_sources()
    _spec, _script, _characters, storyboard = b.documents()
    clips = []
    for shot in spec.shots:
        refs = b.source_refs_for_shot(shot)
        clips.append(b.add(
            f"shot-{shot.id}-video", "video_clip", "atomic.video.generate",
            parameters={
                "prompt": shot.visual_prompt, "duration": shot.duration_seconds,
                "model": spec.providers.video, "resolution": spec.resolution,
                "aspect_ratio": spec.aspect_ratio, "generate_audio": True,
                "generation_mode": "i2v" if refs else "t2v",
                "reference_from_steps": refs,
            }, depends_on=[storyboard, *refs], cost=0.65,
        ))
    return b.finish(clips, transition=0.0, require_audio=True)


def compile_seedance2(workflow: WorkflowSpec, context: PluginContext, spec: VideoSpec) -> RebuildPlan:
    b = WorkflowPlanBuilder(workflow, context, spec)
    b.add_sources()
    _spec, _script, _characters, storyboard = b.documents()
    clips: list[str] = []
    previous_tail = ""
    for shot in spec.shots:
        refs = b.source_refs_for_shot(shot)
        dependencies = [storyboard, *refs, *([previous_tail] if previous_tail else [])]
        parameters: dict[str, Any] = {
            "prompt": shot.visual_prompt, "duration": shot.duration_seconds,
            "provider": "wavespeed", "model": "doubao-seedance-2-0",
            "resolution": spec.resolution,
            "aspect_ratio": spec.aspect_ratio, "generate_audio": True,
            "reference_from_steps": refs, "shot_id": shot.id,
        }
        if previous_tail:
            parameters.update({"generation_mode": "i2v", "start_image_from_step": previous_tail})
        clip = b.add(
            f"shot-{shot.id}-video", "video_clip", "atomic.video.generate",
            parameters=parameters, depends_on=dependencies, cost=0.65,
        )
        clips.append(clip)
        previous_tail = b.add(
            f"shot-{shot.id}-tail", "continuity_frame", "media.extract_frame",
            parameters={"position": "last", "format": "png", "source_video_step": clip},
            depends_on=[clip],
        )
    return b.finish(clips, transition=0.125, require_audio=True)


def compile_short_drama(
    workflow: WorkflowSpec, context: PluginContext, spec: VideoSpec, *, parallel: bool,
) -> RebuildPlan:
    if parallel and any(abs(shot.duration_seconds - 15.0) > 0.01 for shot in spec.shots):
        raise BuildPlanValidationError(
            "short-drama-workflow requires complete 15-second Seedance segments"
        )
    b = WorkflowPlanBuilder(workflow, context, spec)
    b.add_sources()
    _spec, _script, characters, storyboard = b.documents()
    production_manifest = storyboard
    if not parallel:
        outline = b.add(
            "outline", "outline", "runtime.artifact.persist",
            parameters={
                "title": f"{spec.title} outline",
                "content": [shot.beat for shot in spec.shots],
            }, depends_on=[_script],
        )
        scenes = b.add(
            "scenes", "scenes", "runtime.artifact.persist",
            parameters={
                "title": "Scenes",
                "content": spec.workflow_parameters.get("scenes", []),
            }, depends_on=[outline, characters],
        )
        production_manifest = b.add(
            "shots", "shots", "runtime.artifact.persist",
            parameters={
                "title": "Shots",
                "content": [shot.model_dump(mode="json") for shot in spec.shots],
            }, depends_on=[scenes, storyboard],
        )
    refs_by_character = b.character_references(characters)
    clips: list[str] = []
    previous_tail = ""
    for shot in spec.shots:
        refs = [refs_by_character[item] for item in shot.character_ids if item in refs_by_character]
        refs.extend(b.source_refs_for_shot(shot))
        dependencies = [production_manifest, *refs]
        if previous_tail and not parallel:
            dependencies.append(previous_tail)
        parameters: dict[str, Any] = {
            "prompt": shot.visual_prompt, "duration": shot.duration_seconds,
            "duration_seconds": shot.duration_seconds,
            "model": "seedance-2.5" if parallel else spec.providers.video,
            "resolution": spec.resolution, "aspect_ratio": spec.aspect_ratio,
            "generate_audio": True, "generation_mode": "t2v" if parallel else "i2v",
            "reference_from_steps": refs, "character_reference_from_steps": refs,
            "native_dialogue": True,
        }
        if parallel:
            parameters["provider"] = "wavespeed"
        if previous_tail and not parallel:
            parameters["start_image_from_step"] = previous_tail
        clip = b.add(
            f"shot-{shot.id}-video", "video_clip", "atomic.video.generate",
            parameters=parameters, depends_on=dependencies, cost=0.65,
            skills=[] if parallel else ["video-director"],
        )
        clips.append(clip)
        if not parallel:
            previous_tail = b.add(
                f"shot-{shot.id}-tail", "continuity_frame", "media.extract_frame",
                parameters={"position": "last", "source_video_step": clip}, depends_on=[clip],
            )
    if not parallel or not spec.audio.subtitles:
        return b.finish(clips, transition=0.0, require_audio=True)
    timeline = b.add(
        "timeline", "timeline", "media.timeline.compose",
        parameters={"shots": [s.model_dump(mode="json") for s in spec.shots], "video_steps": clips},
        depends_on=clips,
    )
    assembled = b.add(
        "assembled-video", "video_assembled", "media.concat",
        parameters={"video_steps": clips, "normalize": True, "transition_duration": 0.0},
        depends_on=clips, cost=0.02,
    )
    transcription = b.add(
        "transcription", "transcription", "media.transcribe",
        parameters={"video_step": assembled, "language": spec.language},
        depends_on=[assembled],
    )
    subtitles = b.add(
        "subtitles", "subtitle", "media.subtitle.compose",
        parameters={"transcription_step": transcription, "format": "srt"},
        depends_on=[transcription],
    )
    final = b.add(
        "final-video", "final_video", "media.subtitle.burn",
        parameters={
            "video_step": assembled, "subtitle_step": subtitles, "final_output": True,
            "require_audio": True, "require_subtitles": True,
            "target_duration_seconds": spec.target_duration_seconds,
        }, depends_on=[assembled, subtitles], cost=0.02,
    )
    b.add(
        "validate-final", "validation", "cuti.continuity.validate", action="validate",
        parameters={"timeline_step": timeline, "video_step": final},
        depends_on=[*clips, timeline, final],
    )
    topological_steps(b.items)
    return RebuildPlan(
        project_id=b.project_id, kind="initial", base_project_version_id=b.base_version_id,
        workflow_id=workflow.skill_name, video_spec=spec, items=b.items,
        estimated_cost=round(sum(item.estimated_cost for item in b.items), 6),
    )


def compile_product(
    workflow: WorkflowSpec, context: PluginContext, spec: VideoSpec, *, scenario: bool = False,
    multi_reference: bool = False,
) -> RebuildPlan:
    if not spec.source_asset_ids:
        raise BuildPlanValidationError(f"{workflow.skill_name} requires a product source image")
    b = WorkflowPlanBuilder(workflow, context, spec)
    b.add_sources()
    _spec, _script, _characters, storyboard = b.documents()
    product_refs = [b.source_steps[item] for item in spec.source_asset_ids]
    setting = ""
    if workflow.mode == "cuti_product_workflow":
        setting = b.add(
            "product-setting", "product_setting", "atomic.image.generate",
            parameters={
                "prompt": "Create a reusable product environment while preserving exact product identity",
                "model": spec.providers.image, "reference_from_steps": product_refs,
            }, depends_on=[storyboard, *product_refs], cost=0.08,
            skills=list(workflow.skill_dependencies),
        )
    clips: list[str] = []
    for shot in spec.shots:
        refs = [*product_refs, *([setting] if setting else [])]
        clips.append(b.add(
            f"shot-{shot.id}-video", "video_clip", "atomic.video.generate",
            parameters={
                "prompt": shot.visual_prompt, "duration": shot.duration_seconds,
                "model": spec.providers.video, "resolution": spec.resolution,
                "aspect_ratio": spec.aspect_ratio, "generate_audio": True,
                "generation_mode": "t2v" if multi_reference else "i2v",
                "reference_from_steps": refs,
                "product_identity_reference_steps": product_refs,
                "scenario_product_ad": scenario,
            }, depends_on=[storyboard, *refs], cost=0.65,
            skills=list(dict.fromkeys([*workflow.skill_dependencies, "video-director"])),
        ))
        b.add(
            f"shot-{shot.id}-product-validation", "product_validation",
            "cuti.continuity.validate", action="validate",
            parameters={
                "video_step": f"shot-{shot.id}-video",
                "product_reference_steps": product_refs,
            }, depends_on=[f"shot-{shot.id}-video", *product_refs],
        )
    return b.finish(clips, transition=0.0, require_audio=True)


def compile_mv(workflow: WorkflowSpec, context: PluginContext, spec: VideoSpec) -> RebuildPlan:
    """Dest $mv boxes. Spec fields on the step; no second director."""
    b = WorkflowPlanBuilder(workflow, context, spec)
    b.add_sources()
    _spec, _script, characters, storyboard = b.documents()
    identity_images = [
        b.source_steps[item] for item in spec.source_asset_ids
        if item in b.sources and b.sources[item].type in {
            "source_image", "image", "character_reference",
        }
    ]
    refs_by_character = b.character_references(
        characters, skills=[], reference_from_steps=identity_images,
    ) if spec.characters else {}
    wp = spec.workflow_parameters or {}
    uploaded = [
        b.source_steps[item] for item in spec.source_asset_ids
        if b.sources[item].type in {"source_audio", "audio", "audio_bgm"}
    ]
    if uploaded:
        music = uploaded[0]
    elif spec.audio.bgm_prompt:
        music_parameters: dict[str, Any] = {"prompt": spec.audio.bgm_prompt}
        for key in (
            "title", "tags", "custom_mode", "make_instrumental", "instrumental",
            "vocal_gender", "mv", "lyrics", "duration",
        ):
            if key in wp:
                music_parameters[key] = wp[key]
        music_parameters.setdefault("title", spec.title)
        music = b.add(
            "music", "audio_bgm", "suno.generate",
            parameters=music_parameters, depends_on=[storyboard], cost=0.10,
        )
    else:
        raise BuildPlanValidationError(
            "music video workflow requires uploaded audio or audio.bgm_prompt"
        )
    analysis = b.add(
        "music-analysis", "audiomap", "media.audio_analyze",
        parameters={"audio_step": music, "target_duration_sec": spec.target_duration_seconds},
        depends_on=[music],
    )
    cut_parameters: dict[str, Any] = {"audio_step": music, "analysis_step": analysis}
    for key in ("start_sec", "segments"):
        if key in wp:
            cut_parameters[key] = wp[key]
    cut_parameters.setdefault("duration", spec.target_duration_seconds)
    cut = b.add(
        "music-cut", "audio_cut", "media.audio_cut",
        parameters=cut_parameters, depends_on=[music, analysis],
    )
    clips: list[str] = []
    segments = cut_parameters.get("segments")
    for index, shot in enumerate(spec.shots):
        if shot.character_ids:
            look = [
                refs_by_character[item]
                for item in shot.character_ids if item in refs_by_character
            ]
        else:
            look = list(refs_by_character.values())
        refs = list(dict.fromkeys([*look, *b.source_refs_for_shot(shot)]))
        parameters: dict[str, Any] = {
            "prompt": shot.visual_prompt, "duration": shot.duration_seconds,
            "model": spec.providers.video, "resolution": spec.resolution,
            "aspect_ratio": spec.aspect_ratio, "generate_audio": False,
            "reference_from_steps": refs, "audio_reference_from_step": cut,
        }
        if isinstance(segments, list) and index < len(segments):
            parameters["audio_segment_index"] = index
        clips.append(b.add(
            f"shot-{shot.id}-video", "video_clip", "api.provider.generate",
            parameters=parameters, depends_on=[storyboard, cut, *refs], cost=0.65,
        ))
    html = wp.get("caption_html") or wp.get("composition_html") or ""
    return b.finish(
        clips, transition=0.0, require_audio=True, audio_step=cut,
        captions=bool(spec.audio.subtitles and isinstance(html, str) and html.strip()),
    )


def compile_skill_workflow(
    workflow: WorkflowSpec, context: PluginContext, spec: VideoSpec,
) -> RebuildPlan:
    mode = workflow.mode
    if mode in UNAVAILABLE_WORKFLOW_MODES:
        raise BuildPlanValidationError(UNAVAILABLE_WORKFLOW_MODES[mode])
    if mode == "keyframe_pipeline":
        return compile_keyframe(workflow, context, spec)
    if mode == "direct_video":
        return compile_direct(workflow, context, spec)
    if mode == "seedance2":
        return compile_seedance2(workflow, context, spec)
    if mode in {"mv", "seedance_mv"}:
        return compile_mv(workflow, context, spec)
    if mode == "short_drama":
        return compile_short_drama(workflow, context, spec, parallel=False)
    if mode == "short_drama_workflow":
        return compile_short_drama(workflow, context, spec, parallel=True)
    if mode == "product_ad_video":
        return compile_product(workflow, context, spec)
    if mode == "cuti_product_workflow":
        return compile_product(workflow, context, spec)
    if mode == "cuti_scenario_product_workflow":
        return compile_product(workflow, context, spec, scenario=True)
    if mode == "libtv_product_workflow":
        return compile_product(workflow, context, spec, multi_reference=True)
    raise BuildPlanValidationError(
        f"workflow {workflow.skill_name} has no installed compiler for mode {mode}"
    )
