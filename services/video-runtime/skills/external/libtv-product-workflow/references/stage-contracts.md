# Stage contracts

## Plan dependency graph

```text
product_intake
  -> product_identity_contract
  -> creative_brief + visual_bible + sound_bible
  -> sequence_plan + shot_plan
  -> shot_reference_packages
  -> shot_video_candidates
  -> selected_shot_videos
  -> normalized_clips
  -> sound_assets
  -> rough_cut
  -> final_master
```

No storyboard, generated keyframe, first-frame, or last-frame artifact is allowed between `shot_plan` and video generation.

## Product identity contract

```json
{
  "hard_constraints": [],
  "verified_claims": [],
  "guidance": [],
  "unknowns": [],
  "forbidden_fabrications": []
}
```

## Shot plan

```json
{
  "shot_id": "S01",
  "duration_seconds": 8,
  "product_state_in": {},
  "product_state_out": {},
  "framing": "",
  "camera_motion": "",
  "product_action": "",
  "lighting_change": "",
  "reference_roles": [],
  "voiceover": "",
  "sfx": [],
  "transition_intent": "",
  "acceptance_criteria": []
}
```

## Shot reference package

```json
{
  "shot_id": "S01",
  "reference_images": [
    {"index": 1, "url": "", "artifact_id": "", "role": "product_identity", "purpose": ""}
  ],
  "reference_videos": [],
  "reference_audios": [],
  "prompt_bindings": ["@图片1 controls product identity"],
  "identity_coverage": {"required": [], "covered": [], "missing": []}
}
```

## Generation audit fields

Persist on every generation task:

```json
{
  "applied_skill_ids": ["libtv-product-workflow"],
  "resolved_skills": ["libtv-product-workflow"],
  "reference_roles": [],
  "constraint_contract": {},
  "constraint_coverage": {"required": [], "covered": [], "missing": []},
  "final_prompt": "",
  "model": "doubao-seedance-2-0-260128",
  "mode": "reference_to_video",
  "attempt": 1
}
```

Reject the call if required constraint coverage is missing, the model is not Seedance 2, the mode is an I2V mode, or any reference is described as a first frame.

## Cuti capability mapping

| Outcome | Capability |
|---|---|
| Intake, identity, strategy, shot plan, reference packages | `atomic.text.generate` |
| Direct multireference video | `api.provider.generate` or `api.ark_protocol.generate` |
| Music | `atomic.music.generate` |
| Assembly | `media.concat` |
| Captions/advanced edit | enabled OpenMontage capability when requested |

Generate no more than two shots concurrently. Inspect each batch before scheduling the next.
