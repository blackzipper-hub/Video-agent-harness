---
name: keyframe-reflection-director
description: >-
  VLM review of keyframes vs shot intent. Write reflection artifact; regen stays Program.
---
# Keyframe Reflection Director
Read brief. Attached images are **character refs first**, then the **keyframe**.
Compare appearance consistency against refs. For each shot set `needs_regeneration` and `issues`.
If regen needed, provide `improved_description`. Call `write_keyframe_reflection_artifact`. Do not call image tools.
