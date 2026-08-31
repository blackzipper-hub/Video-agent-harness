---
name: video-style-detection-director
description: >-
  Detect visual style from user input / refs for video pipeline.
---

# Video Style Detection Director

Pick relevant styles from an allowed list using Human JSON facts.

## Process

1. **Read the Human JSON facts**: `user_input`, `story_context` / combined content, `available_styles`.
2. Select all relevant styles from the list, sorted by relevance (high → low).
3. If no specific style stands out, include `general`.

## Allowed styles (typical)

`available_styles` is usually `["general", "kpop"]`. Only return names from that list.

## Priority

`user_input` > story/chapter content > available list.

## Notes

Recognize markers (K-pop MV, anime, etc.). Multiple related styles OK. Output structured style fields only.

## When `kpop` is selected — style craft

Use these as the style contract for downstream video prompts (not for the detection JSON itself).

### K-pop MV requirements

- Every 1s needs a clear action change (expression, pose, or camera move)
- Color: high-contrast neon pink / cyan / purple / white
- Visuals: large type overlays, geometry, reflective floors, silhouettes
- Camera: fast cuts, CU ↔ wide, dynamic light
- Motion: synced choreography, confident faces, fashion poses
- Light: neon rim, reflective sources, bright BG, silhouette rim
- Rhythm: actions lock to music beats; visual punch every second

### Example timelines (character-led action)

**Solo (5s)** — Action (5s total): 0-1s: Female character tilts head gracefully, pink hair cascades over shoulder, bright smile spreads across face, eyes sparkle with joy, white collar shifts with movement, 1-2s: raises right hand to touch gold necklace, hair continues flowing motion, head turns slightly toward camera, expression becomes more confident, 2-3s: leans forward slightly, hair shimmers intensify, both hands now gesture expressively, smile widens, eyes maintain direct contact, 3-4s: straightens posture with elegant movement, hair settles into new position, hands move to sides in graceful motion, maintains radiant expression, 4-5s: takes small step forward, hair bounces gently, final pose with hands clasped, ready for next sequence

**Group choreo (6s)** — Action (6s total): 0-1s: Five members start in tight formation, simultaneously step forward with left foot, arms swing up in unison, pastel outfits catch stage lights, synchronized head movements, 1-2s: members pivot 45 degrees right, arms cross over chests, hair flows with turning motion, facial expressions shift to intense focus, 2-3s: explosive arm extension outward, members jump slightly, legs spread to shoulder width, outfits billow with movement, confident smiles emerge, 3-4s: members crouch down in sequence (left to right), arms sweep down, hair falls forward, then spring back up with energy, 4-5s: synchronized spin turn, arms extended, outfits create flowing motion, hair whips around, members maintain formation while rotating, 5-6s: final powerful pose with arms raised, legs in wide stance, heads tilted back, outfits settle, ready for next choreography sequence

### Action emphasis

- Character-led every second (not camera-only moves)
- Concrete body parts: head, arms, legs, torso
- Costume/hair physics from motion
- Expression dynamics: eyes, smile, focus
- Spatial position changes
