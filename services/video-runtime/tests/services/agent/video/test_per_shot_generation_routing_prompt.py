#!/usr/bin/env python3
"""Per-shot generation routing：**仅模板渲染与文案契约**（**不是**真实 LLM 调用、不验证模型输出）。

不 import ``prompts.prompt_loader``（其会链式加载 ``prompt_config`` / 全应用 schema），
仅依赖 langchain_core、模板文件，以及 **importlib 直载** ``per_shot_routing.py``（避免 ``app.schemas`` 包初始化拉 user/email）。

真实端到端需单独集成测试（本文件末尾提供 **skip** 占位）。
"""

import importlib.util
import json
from pathlib import Path

import pytest
from langchain_core.prompts import ChatPromptTemplate


def _import_per_shot_routing_schema_module():
    """只加载 ``per_shot_routing.py`` 单文件，不执行 ``schemas/__init__.py``。"""
    root = Path(__file__).resolve().parents[4]
    path = root / "app" / "schemas" / "per_shot_routing.py"
    spec = importlib.util.spec_from_file_location("_psr_schema_test", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _enum_lines_from_schema() -> dict:
    return _import_per_shot_routing_schema_module().per_shot_routing_enum_lines_for_prompt()


def _sample_capabilities_json() -> str:
    """与 ``get_tool_capabilities`` 注入形状类似的最小样例。"""
    return json.dumps(
        {
            "resolutions": ["480p", "720p", "1080p"],
            "aspect_ratios": ["16:9", "1:1", "9:16"],
            "lipsync_video_tools": [
                {"value": "auto", "label": "Auto"},
                {"value": "ltx_2_3", "label": "WaveSpeed LTX 2.3 Lipsync"},
            ],
            "warnings": [],
        },
        ensure_ascii=False,
        indent=2,
    )


def _load_routing_mustache_template() -> ChatPromptTemplate:
    root = Path(__file__).resolve().parents[4]
    file_path = root / "prompts" / "video" / "per_shot_generation_routing" / "video_per_shot_generation_routing.mustache"
    with open(file_path, encoding="utf-8") as f:
        content = f.read()
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            content = parts[2]
    _, rest = content.split("## System Message", 1)
    system_content, human_content = rest.split("## Human Message", 1)
    return ChatPromptTemplate.from_messages(
        [("system", system_content.strip()), ("human", human_content.strip())],
        template_format="mustache",
    )


def _sample_shots_input() -> list:
    """与线上一致的 8 镜雨天示例（用户提供的 shots_input 结构）。"""
    return [
        {
            "shot_number": 1,
            "shot_type": "近景",
            "scene_description": "细雨中，女主角撑伞，镜头紧密聚焦在她低垂的眼眸，眼神中流露难以言喻的忧郁。雨水从伞沿滴落，她的睫毛微湿，嘴角轻微下沉，背景模糊的雨景与行人匆匆而过，衬托出她的孤寂与内敛。画面色调偏灰蓝，高对比度光影与电影颗粒感，营造王家卫式忧郁氛围。",
            "visual_effects": "雨滴落在伞面与地面，环境雾气，电影颗粒感。",
            "camera_movement": "静止，或极其缓慢的呼吸式推拉，强调情绪",
            "dialogue": "",
            "is_bridge": False,
            "generation_mode": "normal",
        },
        {
            "shot_number": 2,
            "shot_type": "中景",
            "scene_description": "公交车内，女主角侧身静坐，右手轻搭在膝上，左手轻抚车窗玻璃。她的眼神空洞而迷离，凝视着窗外蜿蜒流淌的雨水，思绪万千。车窗外模糊的城市景象和雨滴的流动，形成抽象的灰色线条，与她内心的沉重情绪相呼应。车内环境略显昏暗，灰蓝色调，高对比度光影与电影颗粒感。",
            "visual_effects": "车窗雨水流淌，模糊的景深，电影颗粒感。",
            "camera_movement": "缓慢推镜头，从女主角中景开始，略微推近，强调她脸上的表情和窗外的雨景。",
            "dialogue": "",
            "is_bridge": False,
            "generation_mode": "normal",
        },
        {
            "shot_number": 3,
            "shot_type": "特写",
            "scene_description": "镜头缓慢推近，聚焦于女主角的脸庞和轻抚车窗的右手。她的指尖在冰冷湿润的玻璃上缓慢滑动，感受着雨滴的冰凉。她的眼神深邃而忧郁，睫毛微颤，嘴角紧抿。窗外，雨刷有节奏地左右摆动，瞬间划出一道清晰的视野，随后又被新的雨水迅速模糊，仿佛象征着她内心无法抹去的忧伤。她的脸颊上反射着车窗外的模糊光影，灰蓝色调，高对比度与电影颗粒感。",
            "visual_effects": "车窗雨水流淌与雨刷擦拭效果，水滴在指尖凝聚，电影颗粒感。",
            "camera_movement": "缓慢推镜头，从女主角的胸部以上推至脸部和手部的特写，同时保持焦点的平稳。",
            "dialogue": "",
            "is_bridge": False,
            "generation_mode": "normal",
        },
        {
            "shot_number": 4,
            "shot_type": "中远景",
            "scene_description": "画面从高机位俯拍城市街角，阳光明媚，人群熙攘。女主角身着简约服装，独自静立于街角一隅。镜头缓慢下移并推近，逐渐清晰地展现她的身姿和三分之二侧的侧脸，她头微低，眼神向下凝视。阳光洒在她身上，背景模糊的人群与欢声笑语，形成强烈反差。",
            "visual_effects": "环境光斑，电影颗粒感，浅景深虚化背景人群。",
            "camera_movement": "缓慢推镜头并下移（Dolly in and Pedestal Down），从高机位俯拍逐渐降至平视，聚焦主体。",
            "dialogue": "",
            "is_bridge": False,
            "generation_mode": "normal",
        },
        {
            "shot_number": 5,
            "shot_type": "特写",
            "scene_description": "镜头紧密聚焦在女主角的脸部特写。她微垂眼帘，深吸一口气，眼眶泛红，泪光在眼底打转。她的嘴角轻微抽动，下颚线紧绷，努力克制着即将夺眶而出的泪水。阳光强烈地洒在她脸上，却未能照亮她眼中的悲伤。背景模糊的人群与欢声笑语形成压迫性的对比，凸显她内心独自承受痛苦的脆弱和孤寂。画面保持王家卫式的暖黄色调、高对比度与电影颗粒感。",
            "visual_effects": "泪光闪烁，浅景深虚化背景，电影颗粒感。",
            "camera_movement": "静止，或极其缓慢的呼吸式推拉，强调脸部微表情。",
            "dialogue": "",
            "is_bridge": False,
            "generation_mode": "normal",
        },
        {
            "shot_number": 6,
            "shot_type": "中近景",
            "scene_description": "女主角缓慢走进私密房间，背对镜头，暖黄色阳光透过窗户，在高对比度下勾勒出她的轮廓。她步伐沉重，缓缓走向窗边，肩膀微微下沉，透露出疲惫与无奈。当她停下，镜头从背后推近至她的中近景，聚焦于她紧握的右手拳头。指节因用力而泛白，指甲深陷掌心，无声地积蓄着内心的挣扎。背景窗外光影斑驳，虚化处理，整体呈现王家卫式的暖黄色调和电影颗粒感。",
            "visual_effects": "浅景深虚化背景，电影颗粒感",
            "camera_movement": "缓慢跟拍推镜头，从女主角背影中远景推近至手部中近景",
            "dialogue": "无",
            "is_bridge": False,
            "generation_mode": "normal",
        },
        {
            "shot_number": 7,
            "shot_type": "特写",
            "scene_description": "镜头从女主角侧脸的特写开始，高对比度的暖黄色光线勾勒出她面部的轮廓。她紧闭双眼，眉心紧锁，一滴晶莹的泪水无声地滑过脸颊，下巴微颤。随后，她双手缓慢抬起，紧紧掩住脸庞，肩膀因抽泣而轻微颤抖。阳光在她身后形成一道耀眼的光晕，强化了她内心深处的孤独与崩溃。整个画面充满电影颗粒感，呈现王家卫式的暖黄色调。",
            "visual_effects": "浅景深虚化背景，光晕效果，电影颗粒感",
            "camera_movement": "镜头保持面部特写，有轻微的呼吸感晃动",
            "dialogue": "无",
            "is_bridge": False,
            "generation_mode": "normal",
        },
        {
            "shot_number": 8,
            "shot_type": "特写",
            "scene_description": "镜头从女主角泪痕未干的三分之二侧脸特写开始，她的眼睛微红，眼底仍有湿润。她的眼神在这一过程中逐渐褪去崩溃时的绝望，取而代之的是一种深沉的无奈与淡淡的释然。背景光线柔和地勾勒出她的轮廓，电影颗粒感强烈，营造出回忆与感伤交织的氛围。她轻启双唇，无声地叹息，仿佛低语一个“oh”，是对过往的告别，也是对未来的接纳。画面风格保持王家卫式的暖黄色调、高对比度光影。",
            "visual_effects": "浅景深虚化背景，电影颗粒感",
            "camera_movement": "缓慢推镜头，保持主体侧脸特写",
            "dialogue": "无",
            "is_bridge": False,
            "generation_mode": "lipsync",
        },
    ]


def _render_routing_prompt(
    *,
    detected_language: str = "zh",
    user_image_tool: str = "auto",
    user_video_tool: str = "auto",
    user_lipsync_tool: str = "auto",
) -> str:
    pt = _load_routing_mustache_template()
    shots_json = json.dumps(_sample_shots_input(), ensure_ascii=False, indent=2)
    el = _enum_lines_from_schema()
    messages = pt.format_messages(
        detected_language=detected_language,
        shots_json=shots_json,
        user_image_tool=user_image_tool,
        user_video_tool=user_video_tool,
        user_lipsync_tool=user_lipsync_tool,
        user_capabilities_json=_sample_capabilities_json(),
        **el,
    )
    parts = []
    for m in messages:
        c = m.content
        parts.append(c if isinstance(c, str) else str(c))
    return "\n\n".join(parts)


class TestPerShotGenerationRoutingPromptRender:
    """契约：区分「输入已有 generation_mode」与「每镜工具候选」；枚举行由 schema 注入。"""

    def test_renders_eight_shots_and_user_tools(self):
        text = _render_routing_prompt()
        assert '"shot_number": 1' in text
        assert '"shot_number": 8' in text
        assert text.count('"shot_number"') == 8
        assert '"image_generation_tool": "auto"' in text
        assert '"video_generation_tool_normal_chain": "auto"' in text
        assert '"lipsync_video_tool": "auto"' in text

    def test_wet_scene_bans_wan_in_prompt(self):
        """口型小节：湿画面对 Wan 弱，优先 LTX。"""
        text = _render_routing_prompt()
        assert "lipsync_routing_rules" in text
        assert "wan_2_5" in text
        assert "wan_2_6_flash" in text
        assert "不要" in text or "禁止" in text

    def test_no_internal_pipeline_names(self):
        """LLM 不需要知道的内部实现名不应出现。"""
        text = _render_routing_prompt()
        lowered = text.lower()
        assert "assign_generation_mode_to_shots" not in lowered
        assert "resolve_user_option_for_shot" not in lowered
        assert "useroption" not in lowered.replace(" ", "")

    def test_focuses_on_mode_and_candidate_tools(self):
        text = _render_routing_prompt()
        assert "recommended_generation_mode" in text
        assert "image_generation_tool" in text
        assert "normal_video_tool" in text
        assert "lipsync_video_tool" in text
        assert "lipsync_routing_rules" in text
        assert "image_tool_routing" in text
        assert "normal_video_tool_routing" in text

    def test_generation_mode_is_input_preserved_tools_are_new(self):
        """generation_mode 为输入已有；工具候选为每镜输出（文案区分）。"""
        text = _render_routing_prompt()
        assert "shots_input" in text and "generation_mode" in text
        assert "输入里已有" in text or "输入" in text
        assert "image_tool_routing" in text and "normal_video_tool_routing" in text

    def test_enum_lines_from_schema_appear_in_render(self):
        el = _enum_lines_from_schema()
        text = _render_routing_prompt()
        assert el["enum_image_tools_line"] in text
        assert "`nano_banana`" in el["enum_image_tools_line"]
        assert "wan_2_2_speech_to_video" in el["enum_lipsync_video_tools_line"]


class TestPerShotGenerationRoutingPromptContract:
    """与用户提供的一帧 template_data 形状兼容（shots_json 为字符串）。"""

    def test_user_payload_style_shots_json_string(self):
        """用户侧常把 shots_json 预序列化为字符串；Mustache 应原样嵌入。"""
        payload = {
            "detected_language": "zh",
            "shots_count": 8,
            "shots_json": json.dumps(_sample_shots_input(), ensure_ascii=False, indent=2),
        }
        pt = _load_routing_mustache_template()
        el = _enum_lines_from_schema()
        messages = pt.format_messages(
            detected_language=payload["detected_language"],
            shots_json=payload["shots_json"],
            user_image_tool="auto",
            user_video_tool="auto",
            user_lipsync_tool="auto",
            user_capabilities_json=_sample_capabilities_json(),
            **el,
        )
        full = "\n\n".join(m.content if isinstance(m.content, str) else str(m.content) for m in messages)
        assert payload["shots_count"] == full.count('"shot_number"')
        assert "细雨中" in full
        assert "480p" in full or "720p" in full


@pytest.mark.skip(reason="真实 LLM 结构化输出属集成测试：依赖密钥与计费，不在此仓库默认 CI 跑")
def test_real_llm_per_shot_routing_skipped_placeholder():
    """若需手跑：取消 skip 并在实现中调用 ``ainvoke_structured_resilient`` + ``PerShotGenerationRoutingOutput``。"""
