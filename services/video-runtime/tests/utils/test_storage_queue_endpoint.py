"""S3_ENDPOINT_URL / SQS_ENDPOINT_URL：设置后 boto3 client 使用自定义端点；默认 None 时走真实 AWS。"""
from unittest.mock import patch

from app.config import settings


def _boto_kwargs(mock_client):
    """返回最后一次 boto3.client 调用的 kwargs。"""
    return mock_client.call_args.kwargs


def test_s3_client_uses_aws_by_default(monkeypatch):
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", None, raising=False)
    from app.utils.s3_utils import S3Utils
    with patch("app.utils.s3_utils.boto3.client") as mc:
        S3Utils._build_s3_client()
    kwargs = _boto_kwargs(mc)
    assert "endpoint_url" not in kwargs


def test_s3_client_uses_minio_endpoint_when_set(monkeypatch):
    monkeypatch.setattr(settings, "S3_ENDPOINT_URL", "http://minio:9000", raising=False)
    monkeypatch.setattr(settings, "S3_ACCESS_KEY_ID", "ak", raising=False)
    monkeypatch.setattr(settings, "S3_SECRET_ACCESS_KEY", "sk", raising=False)
    from app.utils.s3_utils import S3Utils
    with patch("app.utils.s3_utils.boto3.client") as mc:
        S3Utils._build_s3_client()
    kwargs = _boto_kwargs(mc)
    assert kwargs["endpoint_url"] == "http://minio:9000"
    assert kwargs["aws_access_key_id"] == "ak"
    assert kwargs["aws_secret_access_key"] == "sk"


def test_sqs_client_uses_endpoint_when_set(monkeypatch):
    monkeypatch.setattr(settings, "SQS_ENDPOINT_URL", "http://elasticmq:9324", raising=False)
    from app.services.aws.sqs_service import SQSTaskService
    with patch("app.services.aws.sqs_service.boto3.client") as mc:
        SQSTaskService(queue_url="http://elasticmq:9324/q/tasks", environment="local")
    # 找到 sqs client 的调用
    sqs_calls = [c for c in mc.call_args_list if c.args and c.args[0] == "sqs"]
    assert sqs_calls, "expected a boto3.client('sqs', ...) call"
    assert sqs_calls[-1].kwargs.get("endpoint_url") == "http://elasticmq:9324"
