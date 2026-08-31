---
name: first-frame-director
description: >-
  Check/revise detailed shots for first-frame I2V compliance. Write first_frame artifact.
---
# First Frame Director
Read first_frame_brief. For each shot: detect violations (reveal full body from partial, subject turn, person enter empty, reveal new object).
If violation, provide revised fields; else has_violation=false.
Respect allow_lipsync. Call `write_first_frame_artifact`. Do not invent new shot numbers.
