"""
智能测试 S3 路径与读写封装 - inner_evaluation/ 目录

所有智能测试相关数据统一放在 S3 前缀 inner_evaluation/ 下。
"""
import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from .s3_utils import s3_utils

logger = logging.getLogger(__name__)

INNER_EVAL_PREFIX = "inner_evaluation"


def _key(*parts: str) -> str:
    return f"{INNER_EVAL_PREFIX}/{'/'.join(parts)}"


# ---------- Baseline prompts (per scenario: t2i / i2i / i2v / t2v) ----------
def baseline_prompts_manifest_key(scenario: str, batch_id: str) -> str:
    return _key("baseline_prompts", scenario, batch_id, "manifest.json")


def baseline_prompts_data_key(scenario: str, batch_id: str) -> str:
    return _key("baseline_prompts", scenario, batch_id, "prompts.json")


def baseline_prompts_prefix(scenario: str) -> str:
    """Prefix for listing batch_id under one scenario (e.g. t2i, i2i, i2v, t2v)."""
    return _key("baseline_prompts", scenario, "")


# ---------- Baseline runs ----------
def baseline_run_config_key(run_id: str) -> str:
    return _key("baseline_runs", run_id, "config.json")


def baseline_run_results_key(run_id: str) -> str:
    return _key("baseline_runs", run_id, "results.json")


def baseline_run_report_key(run_id: str) -> str:
    return _key("baseline_runs", run_id, "report.html")


def baseline_runs_prefix() -> str:
    return _key("baseline_runs", "")


# ---------- Baseline datasets (reference images manifest) ----------
def baseline_datasets_images_manifest_key() -> str:
    return _key("baseline_datasets", "images", "manifest.json")


# ---------- Production prompts variants ----------
def production_variant_manifest_key(variant_id: str) -> str:
    return _key("production_prompts", "variants", variant_id, "manifest.json")


def production_variant_content_key(variant_id: str) -> str:
    """变体下所有 prompt 文件内容存一个 JSON：{ key: content }"""
    return _key("production_prompts", "variants", variant_id, "content.json")


def production_variants_prefix() -> str:
    return _key("production_prompts", "variants", "")


# ---------- Production mock datasets (keyframe/video 测试用) ----------
def production_dataset_manifest_key(dataset_id: str) -> str:
    return _key("production_datasets", dataset_id, "manifest.json")


def production_dataset_shots_key(dataset_id: str) -> str:
    return _key("production_datasets", dataset_id, "shots.json")


def production_dataset_character_images_key(dataset_id: str) -> str:
    return _key("production_datasets", dataset_id, "character_images.json")


def production_datasets_prefix() -> str:
    return _key("production_datasets", "")


# ---------- Comprehensive test runs（全面测试：多 case 并发）----------
def comprehensive_run_manifest_key(run_id: str) -> str:
    return _key("comprehensive_runs", run_id, "manifest.json")


def comprehensive_runs_prefix() -> str:
    return _key("comprehensive_runs", "")


# ---------- Benchmark dataset（从已有 runs 捞数据，用于基准测试）----------
def benchmark_dataset_manifest_key(dataset_id: str) -> str:
    return _key("benchmark_datasets", dataset_id, "manifest.json")


def benchmark_datasets_prefix() -> str:
    return _key("benchmark_datasets", "")


# ---------- Consistency test datasets（一致性测试：单镜头单版本）----------
def consistency_dataset_manifest_key(dataset_type: str, dataset_id: str) -> str:
    """dataset_type: image | video"""
    return _key("consistency_datasets", dataset_type, dataset_id, "manifest.json")


def consistency_datasets_prefix(dataset_type: str) -> str:
    return _key("consistency_datasets", dataset_type, "")


def consistency_run_manifest_key(run_id: str) -> str:
    return _key("consistency_runs", run_id, "manifest.json")


def consistency_runs_prefix() -> str:
    return _key("consistency_runs", "")


# ---------- Full run（功能测试 + 效果测试 一键执行）----------
def full_run_manifest_key(run_id: str) -> str:
    return _key("full_runs", run_id, "manifest.json")


def full_runs_prefix() -> str:
    return _key("full_runs", "")


# ---------- Production runs ----------
def production_run_config_key(run_id: str) -> str:
    return _key("production_runs", run_id, "config.json")


def production_run_results_key(run_id: str) -> str:
    return _key("production_runs", run_id, "results.json")


def production_run_report_key(run_id: str) -> str:
    return _key("production_runs", run_id, "report.html")


# ---------- Generic put/get/list (using s3_utils bucket) ----------
async def put_json(key: str, data: Dict[str, Any]) -> bool:
    """Upload JSON to S3. Returns True on success."""
    try:
        body = json.dumps(data, ensure_ascii=False, indent=2)
        await s3_utils.upload_file(body.encode("utf-8"), key, content_type="application/json")
        return True
    except Exception as e:
        logger.exception("eval_s3 put_json failed: key=%s, e=%s", key, e)
        return False


async def get_json(key: str) -> Optional[Dict[str, Any]]:
    """Download and parse JSON from S3. Returns None on failure or missing."""
    try:
        import tempfile
        import os
        import asyncio
        with tempfile.NamedTemporaryFile(delete=False, suffix=".json") as f:
            tmp = f.name
        try:
            ok = await s3_utils.download_file(key, tmp)
            if not ok:
                return None
            def _read():
                with open(tmp, "r", encoding="utf-8") as f:
                    return json.load(f)
            return await asyncio.to_thread(_read)
        finally:
            def _cleanup():
                if os.path.exists(tmp):
                    os.unlink(tmp)
            await asyncio.to_thread(_cleanup)
    except Exception as e:
        logger.warning("eval_s3 get_json failed: key=%s, e=%s", key, e)
        return None


async def put_text(key: str, text: str, content_type: str = "text/html; charset=utf-8") -> bool:
    """Upload text (e.g. HTML) to S3."""
    try:
        await s3_utils.upload_file(text.encode("utf-8"), key, content_type=content_type)
        return True
    except Exception as e:
        logger.exception("eval_s3 put_text failed: key=%s, e=%s", key, e)
        return False


def get_cdn_url(key: str) -> str:
    """Return CDN URL for an S3 key."""
    from ..config import get_settings
    settings = get_settings()
    cdn = (settings.CDN_DOMAIN or "").strip().rstrip("/")
    return f"{cdn}/{key}"


def _list_run_ids_sync(prefix: str, limit: int) -> List[str]:
    """Sync S3 list (run in executor to avoid blocking event loop)."""
    import boto3
    from ..config import get_settings
    settings = get_settings()
    bucket = settings.S3_BUCKET_NAME
    client = boto3.client("s3")
    paginator = client.get_paginator("list_objects_v2")
    run_ids: List[str] = []
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for prefix_obj in page.get("CommonPrefixes") or []:
            p = prefix_obj.get("Prefix", "")
            if p.endswith("/"):
                p = p[:-1]
            run_id = p.split("/")[-1]
            if run_id:
                run_ids.append(run_id)
            if len(run_ids) >= limit:
                return run_ids
    return run_ids


async def list_run_ids(prefix: str, limit: int = 100) -> List[str]:
    """List run_id subdirs under prefix (e.g. baseline_runs). Non-blocking."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _list_run_ids_sync(prefix, limit))
    except Exception as e:
        logger.warning("eval_s3 list_run_ids failed: prefix=%s, e=%s", prefix, e)
        return []


def _delete_key_sync(key: str) -> bool:
    """Sync delete one S3 object. Used by delete_key in executor."""
    try:
        import boto3
        from ..config import get_settings
        settings = get_settings()
        bucket = settings.S3_BUCKET_NAME
        client = boto3.client("s3")
        client.delete_object(Bucket=bucket, Key=key)
        return True
    except Exception as e:
        logger.warning("eval_s3 delete_key failed: key=%s, e=%s", key, e)
        return False


async def delete_key(key: str) -> bool:
    """Delete one object from S3. Non-blocking."""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: _delete_key_sync(key))
    except Exception as e:
        logger.warning("eval_s3 delete_key failed: key=%s, e=%s", key, e)
        return False


def generate_run_id() -> str:
    return uuid.uuid4().hex[:12]


def generate_batch_id() -> str:
    return uuid.uuid4().hex[:12]
