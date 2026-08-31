"""
测试 consistency run 结果中是否包含前端展示所需字段（consistency_display_lines、attempt_details、reason 等）。

前端「一致性测试结果」弹窗依赖：
- consistency_display_lines：与候选详情同一套 DisplayLinesBlock（尝试 N、当次图片/视频、说明、建议 prompt）
- attempt_details：每次尝试的 model、passed、image_url、reason
- reason：case 级原因

运行（需在项目根目录）：
  python -m pytest tests/tools/test_consistency_run_result_display_lines.py -v
  或
  python -m unittest tests.tools.test_consistency_run_result_display_lines -v
"""
import unittest

from app.crud.error_tracking import (
    build_image_consistency_display_lines,
    build_video_consistency_display_lines,
)


class TestConsistencyRunResultDisplayLines(unittest.TestCase):
    """确保 run result case 中 consistency_display_lines 与 attempt_details 结构满足前端展示。"""

    def test_build_image_consistency_display_lines_contains_attempt_and_reason(self):
        """image：metrics 含 consistency_details 时，display_lines 含「尝试 N」「综合说明:」和「当次图片:」。"""
        metrics = {
            "consistency_details": [
                {
                    "model": "gemini-3.1-flash-image-preview",
                    "passed": False,
                    "image_url": "https://cdn.example.com/img.webp",
                    "prompt_used": "全景，角色从 image 2 保持完全不变…",
                    "reason": "脸与参考图不一致，五官有偏差。",
                    "suggested_prompt": None,
                },
                {
                    "model": "gemini-3-pro-image-preview",
                    "passed": True,
                    "image_url": "https://cdn.example.com/img2.webp",
                    "prompt_used": "全景镜头，参考图2中的女性角色…",
                    "reason": None,
                },
            ],
        }
        lines = build_image_consistency_display_lines(metrics)
        self.assertIsInstance(lines, list, "应返回 list")
        self.assertGreater(len(lines), 0, "应有内容")
        # 尝试 1 块
        self.assertTrue(
            any("尝试 1" in line for line in lines),
            "应包含「尝试 1」",
        )
        self.assertTrue(
            any(line.strip().startswith("综合说明:") for line in lines),
            "应包含「综合说明:」行（前端展示原因）",
        )
        self.assertTrue(
            any("当次图片:" in line for line in lines),
            "应包含「当次图片:」",
        )
        # 综合说明内容应来自 reason
        reason_lines = [l for l in lines if l.strip().startswith("综合说明:")]
        self.assertGreater(len(reason_lines), 0)
        self.assertIn("脸与参考图不一致", reason_lines[0], "综合说明应包含第一条 attempt 的 reason")

    def test_build_image_consistency_display_lines_empty_metrics(self):
        """metrics 为空或无 consistency_details 时返回空列表。"""
        self.assertEqual(build_image_consistency_display_lines(None), [])
        self.assertEqual(build_image_consistency_display_lines({}), [])
        self.assertEqual(build_image_consistency_display_lines({"consistency_details": []}), [])

    def test_build_video_consistency_display_lines_contains_attempt_and_reason(self):
        """video：metrics 含 consistency_details 时，display_lines 含「尝试 N」「汇总:」和「当次视频:」。"""
        metrics = {
            "consistency_details": [
                {
                    "model": "some-video-model",
                    "passed": False,
                    "video_url": "https://cdn.example.com/vid.mp4",
                    "i2v_prompt": "固定机位，角色微微点头…",
                    "first_frame_consistency": "poor",
                    "reason_overall": "首帧景别违规：视频露出下半身而首帧未展示。",
                },
            ],
        }
        lines = build_video_consistency_display_lines(metrics)
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)
        self.assertTrue(any("尝试 1" in line for line in lines))
        self.assertTrue(any("汇总:" in line for line in lines))
        self.assertTrue(any("当次视频:" in line for line in lines))
        summary_lines = [l for l in lines if "汇总:" in l]
        self.assertGreater(len(summary_lines), 0)
        self.assertIn("首帧景别违规", summary_lines[0])

    def test_run_result_case_structure_image(self):
        """模拟 worker 写入的 image case 结构：必须包含 consistency_display_lines、attempt_details。"""
        attempt_details = [
            {"model": "m1", "passed": False, "image_url": "https://a/b.webp", "reason": "原因A"},
            {"model": "m2", "passed": True, "image_url": "https://a/c.webp"},
        ]
        metrics = {"consistency_details": attempt_details}
        # 与 worker 一致：写入 result 时带 consistency_display_lines
        consistency_display_lines = build_image_consistency_display_lines(metrics)
        result = {
            "item_id": "case-1",
            "type": "image",
            "passed": False,
            "reason": "最终未通过",
            "image_url": "https://a/c.webp",
            "attempt_count": len(attempt_details),
            "attempt_details": attempt_details,
            "consistency_display_lines": consistency_display_lines,
        }
        self.assertIn("consistency_display_lines", result, "前端依赖此字段做 DisplayLinesBlock")
        self.assertIn("attempt_details", result)
        self.assertGreaterEqual(len(result["consistency_display_lines"]), 2, "至少有多行（尝试 N + 当次图片/说明等）")
        self.assertTrue(all(isinstance(l, str) for l in result["consistency_display_lines"]))

    def test_run_result_case_structure_video(self):
        """模拟 worker 写入的 video case 结构：必须包含 consistency_display_lines、attempt_details。"""
        attempt_details = [
            {"model": "v1", "passed": False, "video_url": "https://a/v.mp4", "reason_overall": "首帧不一致"},
        ]
        metrics = {"consistency_details": attempt_details}
        consistency_display_lines = build_video_consistency_display_lines(metrics)
        result = {
            "item_id": "case-2",
            "type": "video",
            "passed": False,
            "reason": "首帧不一致",
            "video_url": "https://a/v.mp4",
            "attempt_count": 1,
            "attempt_details": attempt_details,
            "consistency_display_lines": consistency_display_lines,
        }
        self.assertIn("consistency_display_lines", result)
        self.assertIn("attempt_details", result)
        self.assertGreater(len(result["consistency_display_lines"]), 0)
        self.assertTrue(any("尝试 1" in l for l in result["consistency_display_lines"]))

    def test_frontend_receives_display_lines_in_manifest(self):
        """GET /consistency-runs/{id} 返回的 manifest.cases 每项必须含 consistency_display_lines，前端才能显示与候选详情一致的 DisplayLinesBlock。"""
        # 模拟 worker 写入后 GET 返回的 manifest 结构（与 get_consistency_run 返回的 data 一致）
        metrics_img = {"consistency_details": [{"model": "m1", "passed": True, "image_url": "https://x/y.webp", "reason": "通过"}]}
        metrics_vid = {"consistency_details": [{"model": "v1", "passed": False, "video_url": "https://x/v.mp4", "reason_overall": "景别违规"}]}
        manifest = {
            "run_id": "test-run-1",
            "status": "completed",
            "cases": [
                {
                    "item_id": "c1",
                    "type": "image",
                    "passed": True,
                    "attempt_details": metrics_img["consistency_details"],
                    "consistency_display_lines": build_image_consistency_display_lines(metrics_img),
                },
                {
                    "item_id": "c2",
                    "type": "video",
                    "passed": False,
                    "attempt_details": metrics_vid["consistency_details"],
                    "consistency_display_lines": build_video_consistency_display_lines(metrics_vid),
                },
            ],
        }
        # 前端拿到的就是 manifest（ResponseModel.success(data=manifest)）
        for i, c in enumerate(manifest["cases"]):
            self.assertIn("consistency_display_lines", c, f"case[{i}] 必须有 consistency_display_lines 前端才显示尝试详情")
            lines = c["consistency_display_lines"]
            self.assertIsInstance(lines, list, f"case[{i}].consistency_display_lines 应为 list")
            self.assertGreater(len(lines), 0, f"case[{i}] 应有展示行")
            self.assertTrue(any("尝试 " in str(l) for l in lines), f"case[{i}] 应包含「尝试 N」")


if __name__ == "__main__":
    unittest.main()
