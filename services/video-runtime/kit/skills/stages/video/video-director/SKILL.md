---
name: video-director
description: >-
  Write Seedance-grade motion prompts (OM seedance-2-0 Higgsfield methodology).
  Opener-first director briefs — identity lock, timecodes, says, camera IS/ISN'T.
---

# Video Director (OM Seedance bar)

Gold source: OpenMontage `.agents/skills/seedance-2-0/SKILL.md` (Higgsfield).
Director prose, not form fields.

**CRITICAL:** Open every prompt with a shot-structure declaration BEFORE story.
Seedance rewards format-upfront — this is the single biggest quality lever.

**CRITICAL — Environment in line 1:** After the format opener, the **same first sentence** MUST name
`place + time-of-day + light`. Close-up / reaction shots are not exempt — still need a place noun.
Never invent an empty plate / studio void as the scene place.
**Place lock:** The place noun MUST be taken from this shot’s `scene_description` (e.g. 学院大厅).
Do **not** invent a different location (废弃工厂、洞穴、祭坛…) when the brief already names one.
If this shot’s brief has **no** place noun: use brief `continuity_place` (story-level location)
or a place noun from other shots in the same brief — never write `场景内` / bare `石柱旁` without a hall/room noun.
**Time-of-day lock:** Use an explicit cue from {室内夜戏, 夜戏, 日戏, 夜晚, 黄昏, 雨夜, 清晨} —
`昏暗` / `傍晚` alone is not enough; pair light with one of those time tags.

**CRITICAL — Reference images = identity lock only:** Lock face/hair/costume/prop. Stack:
`同一角色` / `maintain exact appearance from reference image` /
`no deformation, no drift, no face morph` / `Do not alter clothing category or primary color`.
Do NOT take scene place or bg color from character sheets (Cuti sheets often use gray studio cyclorama).
If needed: `忽略角色参考图背景与浅灰棚底，场景以文字场所为准`.

## Shape

```
[opener]；[place]；[time-of-day]；[light]；16:9；35mm；[texture]。
固定角色：name + costume + prop（参考图只锁外貌不锁背景）。
0–3 秒：…接触/机位…
3–7 秒：…角色（情绪）说：「完整台词」
运镜：…；不要乱切；不要变焦砸脸。
声音：…现场音效…；清晰普通话对白；不要配乐。
不要卡通、不要特效感。
```

## Opener bank (format only — fill [place]/[time]/[light] from THIS shot brief only)

Dialogue:
`单镜头连续电影级对白戏，不要乱切，35mm胶片质感，浅景深，写实皮肤与织物，禁止3D卡通VFX感；[place]，[time]，[light]；16:9。`

Multi-beat:
`蒙太奇多镜头电影级叙事，不要单一机位一条过，电影光，35mm，运动模糊，禁止3D卡通VFX感；[place]，[time]，[light]；16:9。`

CU/reaction:
`一条连续近景对白戏，不要乱切，不要变焦砸脸，35mm胶片颗粒；[place]，[time]，[light]；16:9。`

Do not replace [place] with a location absent from the brief / `continuity_place`.

## Required body

1. Environment — place + time-of-day + light on line 1; light must match time (no 明亮正午+霓虹夜).
2. Identity lock — name + 3–6 attrs; restate in timed blocks; refs = appearance only.
3. Secondary cast same density when on screen.
4. Temporal choreography covering full duration (~2.5s/beat).
5. Says when brief has dialogue: `角色名（情绪）说：「…」` — full meaning; never only “说话状态”.
6. Preserve contact/plot beats from the shot brief (do not drop or rewrite away key actions).
7. Camera IS/ISN'T: e.g. `OTS→CU 微推；不要乱切；不要变焦砸脸`.
8. Audio: diegetic SFX + 清晰普通话对白；不要配乐 (or 不要对白 when silent).

Prefer `t2v_prompt` = same director brief; `i2v_prompt` may mirror it for pipeline compatibility.

## What to avoid

| Don't | Why |
|---|---|
| Light-only or format-only opener (no place noun) | Seedance loses location |
| Invented place not in the shot brief | Story/location rewrite |
| Form shells (`场景：`/`光线：`/`动作（共` field labels) | Workflow stub, not director prose |
| Readable UI / logos / subtitles painted by Seedance | Unreliable; compose later |
| Mix conflicting lighting | Model picks one |
| Drop brief dialogue or contact beats | Story rewrite |
| Aesthetic fluff only (氛围/史诗) without action | Empty clip |

## Duration

- Hero dialogue / reveal: 5–8s substance in timed beats
- Multi-beat montage: 10–12s worth of beats when shot allows
- Inserts: ~4s punch

## Lipsync

When `generation_mode=lipsync` (or the shot is marked lip-sync): show a **singing / lip-sync performance** state. **Do not write lyrics** — mouth timing is system-handled. No on-screen lyric text.

## Checklist

- [ ] Line 1: place + time-of-day + light (place from brief / continuity_place)
- [ ] Identity locked; ref background ignored
- [ ] Dialogue → 说：「…」 when brief has speech
- [ ] Camera IS/ISN'T; no readable UI asked of Seedance
- [ ] Brief contact/plot beats preserved

## Persist

`write_video_artifact` only. If `VALIDATION_ERROR`, fix and call again in the same turn.

Python does **not** rewrite your prompt after write (no Character-says inject, no beat padding, no craft regex gate).
Everything above must already be in `i2v_prompt` / `t2v_prompt` when you call the tool.
