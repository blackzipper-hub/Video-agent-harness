"""Regenerate kit/schemas/artifacts from app.contracts (artifacts only).

LLM/DB contracts live in Python (app.contracts.llm / .db); no JSON export layer.

  PYTHONPATH=. python -m app.contracts.export_json_schemas
"""
from __future__ import annotations

import json
from pathlib import Path

from app.contracts.artifacts.analysis import AnalysisArtifact
from app.contracts.artifacts.analysis_brief import AnalysisBriefArtifact, AudioContextArtifact
from app.contracts.artifacts.outline import OutlineArtifact
from app.contracts.artifacts.scene import ScenesArtifact
from app.contracts.artifacts.script import ScriptArtifact
from app.contracts.artifacts.video import VideoArtifact


def _kit_schemas_root() -> Path:
    return Path(__file__).resolve().parents[2] / "kit" / "schemas"


def export() -> None:
    root = _kit_schemas_root()
    # Only artifact JSON schemas are exported for agent/OM alignment.
    # llm + db: use app.contracts Python types; do not maintain parallel JSON.
    mapping = {
        root / "artifacts" / "analysis.schema.json": AnalysisArtifact,
        root / "artifacts" / "outline.schema.json": OutlineArtifact,
        root / "artifacts" / "analysis_brief.schema.json": AnalysisBriefArtifact,
        root / "artifacts" / "audio_context.schema.json": AudioContextArtifact,
        root / "artifacts" / "script.schema.json": ScriptArtifact,
        root / "artifacts" / "scenes.schema.json": ScenesArtifact,
        root / "artifacts" / "video_prompts.schema.json": VideoArtifact,
    }
    for path, model in mapping.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(model.model_json_schema(), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print("wrote", path.relative_to(root.parents[1]))


if __name__ == "__main__":
    export()
