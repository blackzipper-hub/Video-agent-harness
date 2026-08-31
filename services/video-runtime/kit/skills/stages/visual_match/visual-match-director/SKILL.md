---
name: visual-match-director
description: >-
  Match characters/objects/locations to each scene. Write visual_match artifact.
---
# Visual Match Director
Read visual_match_brief. characters[].index is 0-based for matched_characters.character_index.
Match only elements that appear or are needed. `characters[].index` is **1-based** for `matched_characters.character_index`.
Call `write_visual_match_artifact` with one entry per scene.
