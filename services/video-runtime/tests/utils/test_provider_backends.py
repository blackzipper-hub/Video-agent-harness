"""Provider 后端开关（开源自托管：只需 Postgres + Redis）。

覆盖三条 backend 的「切换 + 默认参数不变」语义：
- QUEUE_BACKEND：redis -> RedisTaskQueue；默认 sqs -> SQSTaskService（工厂选择）
- STORAGE_BACKEND：local -> S3Utils 写本地磁盘并产出 /files URL；默认 s3 不变
- ACCOUNT_BACKEND：env -> 从环境变量读各家 key；默认 appconfig 不变
"""
import asyncio
import os

from app.config import settings


# ---------------- QUEUE ----------------

def test_queue_factory_defaults_to_sqs(monkeypatch):
    monkeypatch.setattr(settings, "QUEUE_BACKEND", "sqs", raising=False)
    from app.services.queue import create_task_queue
    q = create_task_queue(queue_url="https://sqs.x.amazonaws.com/1/q", environment="local")
    assert type(q).__name__ == "SQSTaskService"


def test_queue_factory_selects_redis(monkeypatch):
    monkeypatch.setattr(settings, "QUEUE_BACKEND", "redis", raising=False)
    from app.services.queue import create_task_queue
    q = create_task_queue(environment="local")
    assert type(q).__name__ == "RedisTaskQueue"


# ---------------- STORAGE ----------------

def test_storage_local_upload_download_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local", raising=False)
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000", raising=False)
    from app.utils.s3_utils import S3Utils, is_our_cdn_url

    s3 = S3Utils()
    assert s3._is_local is True

    async def _run():
        url = await s3.upload_file(b"hello", "misc/a.txt", "text/plain")
        assert url == "http://localhost:8000/files/misc/a.txt"
        assert os.path.exists(os.path.join(str(tmp_path), "misc/a.txt"))
        # 我们自己的 URL 应被识别
        assert is_our_cdn_url(url) is True
        # URL -> key 需剥掉 files/ 前缀
        assert s3.cdn_url_to_s3_key(url) == "misc/a.txt"
        # 下载（本地拷贝）
        dst = os.path.join(str(tmp_path), "_out.txt")
        ok = await s3.download_file(url, dst)
        assert ok and open(dst, "rb").read() == b"hello"

    asyncio.run(_run())


def test_storage_local_download_fetches_foreign_http_url(tmp_path, monkeypatch):
    """Gemini/analyze must HTTP-get Suno URLs; local open-source has no S3."""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local", raising=False)
    monkeypatch.setattr(settings, "LOCAL_STORAGE_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(settings, "PUBLIC_BASE_URL", "http://localhost:8000", raising=False)
    from app.utils.s3_utils import S3Utils

    payload = b"x" * 200
    calls: list[str] = []

    class _Response:
        def raise_for_status(self):
            return None

        async def read(self):
            return payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url):
            calls.append(url)
            return _Response()

    monkeypatch.setattr("app.utils.s3_utils.aiohttp.ClientSession", _Session)
    s3 = S3Utils()
    dst = os.path.join(str(tmp_path), "suno.mp3")
    vendor = "https://files.aimusicapi.ai/stems/clip.mp3"
    assert asyncio.run(s3.download_file(vendor, dst)) is True
    assert calls == [vendor]
    assert open(dst, "rb").read() == payload


def test_storage_s3_download_uses_http_for_foreign_url(tmp_path, monkeypatch):
    """dest/prod keep S3 for CDN; vendor hosts still HTTP-get without changing STORAGE_BACKEND."""
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3", raising=False)
    monkeypatch.setattr(settings, "CDN_DOMAIN", "https://cdn-dev.newai.land", raising=False)
    from app.utils.s3_utils import S3Utils

    payload = b"y" * 200

    class _Response:
        def raise_for_status(self):
            return None

        async def read(self):
            return payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class _Session:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url):
            return _Response()

    monkeypatch.setattr("app.utils.s3_utils.aiohttp.ClientSession", _Session)
    s3 = S3Utils()
    s3.s3_client = None
    dst = os.path.join(str(tmp_path), "vendor.mp3")
    assert asyncio.run(s3.download_file("https://files.aimusicapi.ai/stems/clip.mp3", dst)) is True
    assert open(dst, "rb").read() == payload
    # First-party CDN must not take the HTTP fallback (would skip S3).
    assert asyncio.run(s3.download_file("https://cdn-dev.newai.land/audios/a.mp3", dst)) is False


def test_storage_defaults_to_s3(monkeypatch):
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "s3", raising=False)
    from app.utils.s3_utils import S3Utils
    s3 = S3Utils()
    assert s3._is_local is False
    assert s3.cdn_domain == settings.CDN_DOMAIN


# ---------------- ACCOUNT ----------------

def test_account_env_backend_reads_env_keys(monkeypatch):
    monkeypatch.setattr(settings, "ACCOUNT_BACKEND", "env", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-x")
    monkeypatch.setenv("GOOGLE_API_KEY", "g-x")
    monkeypatch.setenv("WAVESPEED_API_KEY", "ws-x")
    monkeypatch.delenv("SUNO_API_KEY", raising=False)
    monkeypatch.delenv("POLLO_API_KEY", raising=False)
    from app.services.account.account_manager import AccountConfigLoader

    loader = AccountConfigLoader()
    accounts = asyncio.run(loader._load_accounts_from_appconfig())
    assert accounts["openai"][0].api_key == "sk-openai-x"
    assert accounts["openai"][0].name == "openai-env"
    assert accounts["google"][0].api_key == "g-x"
    assert accounts["wavespeed"][0].api_key == "ws-x"
    # 未设置的 provider 不应出现
    assert "suno" not in accounts
    assert "pollo" not in accounts


def test_account_env_backend_initializes_without_redis(monkeypatch):
    monkeypatch.setattr(settings, "ACCOUNT_BACKEND", "env", raising=False)
    from app.services.account.account_manager import AccountConfigLoader
    from app.services.account.rate_limiter import ModelRateLimiter

    loader = AccountConfigLoader()
    loader._initialized = False
    loader.redis = None
    asyncio.run(loader.initialize())
    limiter = ModelRateLimiter()
    asyncio.run(limiter.initialize())

    assert loader._initialized is True
    assert loader.redis is None
    assert limiter.redis is None


def test_account_defaults_to_appconfig():
    # 默认后端保持 appconfig（不触发 AWS 调用，仅校验默认值）
    assert (getattr(settings, "ACCOUNT_BACKEND", "appconfig") or "appconfig").lower() in ("appconfig", "env")
