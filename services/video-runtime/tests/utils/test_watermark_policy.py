import inspect

from app.utils.s3_utils import S3Utils


def test_canonical_video_ingest_defaults_to_no_watermark():
    parameter = inspect.signature(S3Utils.ensure_video_on_our_s3).parameters["watermark"]
    assert parameter.default is False
