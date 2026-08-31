"""
测试 Gemini 音频 file_uri + LangChain 组合（模拟生产代码路径）。

目的：隔离 400 错误的根因。生产代码使用 LangChain 而非原生 google.genai SDK，
需要验证 file_uri 在 LangChain 路径下是否兼容 with_structured_output。

运行方式：
  conda activate cuti-video-local
  export GOOGLE_API_KEY=your_key
  cd /home/songsong/local/Cuti-VideoAgent
  python tests/tools/test_gemini_audio_file_uri.py

测试矩阵：
  Step 1: 上传音频到 Google → 检查返回的 uri 格式
  Step 2: LangChain + file_uri + 无 structured_output（纯文本回复）
  Step 3: LangChain + file_uri + with_structured_output（生产路径）
  Step 4: LangChain + inline base64 + with_structured_output（对照组）
  Step 5: （如 Step3 失败）LangChain + file_uri，手动解析 JSON 代替 structured_output

根据哪一步成功/失败，即可确定修复方案。
"""
import os
import sys
import time
import base64

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)


# ---------- 辅助 ----------
def _find_test_audio() -> str | None:
    """找测试音频。优先用小一点的文件快速跑，但必须 >15MB 触发 file_uri 路径。"""
    fixtures = os.path.join(PROJECT_ROOT, "tests", "fixtures")
    for name in ("test_under_70mb.wav", "test_under_70mb.mp3"):
        p = os.path.join(fixtures, name)
        if os.path.exists(p):
            return p
    return None


def _divider(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ================================================================
# Step 1: 上传音频到 Google，检查返回的 uri 格式
# ================================================================
def step1_upload(audio_path: str):
    """上传音频到 Google Files API，返回 (client, uploaded_file)。"""
    from google import genai

    _divider("Step 1: 上传音频到 Google Files API")
    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        print("  ❌ SKIP: 需要 GOOGLE_API_KEY 或 GEMINI_API_KEY")
        return None, None

    client = genai.Client(api_key=api_key)
    file_size = os.path.getsize(audio_path) / (1024 * 1024)
    print(f"  文件: {audio_path}")
    print(f"  大小: {file_size:.2f} MB")

    uploaded = client.files.upload(file=audio_path)
    print(f"  上传成功!")
    print(f"  .name:      {repr(uploaded.name)}")
    print(f"  .uri:       {repr(uploaded.uri)}")
    print(f"  .mime_type: {repr(getattr(uploaded, 'mime_type', None))}")
    print(f"  .state:     {getattr(uploaded, 'state', None)}")

    # 等待处理完成
    if hasattr(uploaded, "state") and str(getattr(uploaded.state, "name", "")) == "PROCESSING":
        print("  等待处理完成...")
        for i in range(90):
            time.sleep(2)
            uploaded = client.files.get(name=uploaded.name)
            state = str(getattr(uploaded.state, "name", ""))
            if state == "ACTIVE":
                print(f"  ✅ 处理完成 ({(i+1)*2}s)")
                break
            if i % 5 == 4:
                print(f"  ... 状态: {state}, 已等待 {(i+1)*2}s")
        else:
            print("  ⚠️ 超时，继续尝试")

    print(f"  最终 .uri: {repr(uploaded.uri)}")
    return client, uploaded


# ================================================================
# Step 2: LangChain + file_uri + 无 structured_output（纯文本）
# ================================================================
def step2_langchain_file_uri_plain(file_uri: str, mime_type: str):
    """用 LangChain ChatGoogleGenerativeAI 发送 file_uri，不用 structured_output。"""
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage

    _divider("Step 2: LangChain + file_uri（无 structured_output）")
    print(f"  file_uri: {repr(file_uri)}")
    print(f"  mime_type: {repr(mime_type)}")

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    message = HumanMessage(content=[
        {"type": "text", "text": "用一句话描述这段音频的内容。"},
        {"type": "media", "file_uri": file_uri, "mime_type": mime_type},
    ])

    try:
        response = llm.invoke([message])
        text = response.content if hasattr(response, "content") else str(response)
        print(f"  ✅ 成功! 回复: {repr(text[:200])}")
        return True
    except Exception as e:
        print(f"  ❌ 失败: {e}")
        return False


# ================================================================
# Step 3: LangChain + file_uri + with_structured_output（生产路径）
# ================================================================
def step3_langchain_file_uri_structured(file_uri: str, mime_type: str):
    """用 LangChain + with_structured_output + file_uri，完全模拟生产代码。"""
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage
    from pydantic import BaseModel, Field
    from typing import List

    _divider("Step 3: LangChain + file_uri + with_structured_output（生产路径）")
    print(f"  file_uri: {repr(file_uri)}")

    # 简化版的结构化输出模型（模拟 GeminiTranscriptionResult）
    class SimpleAudioResult(BaseModel):
        """简化的音频分析结果"""
        description: str = Field(description="音频内容描述")
        language: str = Field(description="识别到的语言")
        duration_estimate: str = Field(description="预估时长")

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    structured_llm = llm.with_structured_output(SimpleAudioResult, include_raw=True)

    message = HumanMessage(content=[
        {"type": "text", "text": "分析这段音频，返回内容描述、语言、预估时长。"},
        {"type": "media", "file_uri": file_uri, "mime_type": mime_type},
    ])

    try:
        result = structured_llm.invoke([message])
        parsed = result.get("parsed") if isinstance(result, dict) else result
        print(f"  ✅ 成功! 解析结果: {parsed}")
        return True
    except Exception as e:
        print(f"  ❌ 失败: {e}")
        return False


# ================================================================
# Step 4: LangChain + inline base64 + with_structured_output（对照组）
# ================================================================
def step4_langchain_inline_structured(audio_path: str):
    """对照组：用 inline base64 + structured_output，确认 structured_output 本身没问题。"""
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage
    from pydantic import BaseModel, Field

    _divider("Step 4: LangChain + inline base64 + with_structured_output（对照组）")

    # 读取前 2MB 做截断（仅测试连通性，不需要完整音频）
    with open(audio_path, "rb") as f:
        audio_bytes = f.read(2 * 1024 * 1024)
    encoded = base64.b64encode(audio_bytes).decode("utf-8")
    print(f"  使用前 {len(audio_bytes)/(1024*1024):.2f} MB 做 inline base64")

    class SimpleAudioResult(BaseModel):
        description: str = Field(description="音频内容描述")
        language: str = Field(description="识别到的语言")

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)
    structured_llm = llm.with_structured_output(SimpleAudioResult, include_raw=True)

    ext = os.path.splitext(audio_path)[1].lower()
    mime = {"wav": "audio/wav", "mp3": "audio/mpeg"}.get(ext.lstrip("."), "audio/wav")

    message = HumanMessage(content=[
        {"type": "text", "text": "分析这段音频，返回内容描述、语言。"},
        {"type": "media", "data": encoded, "mime_type": mime},
    ])

    try:
        result = structured_llm.invoke([message])
        parsed = result.get("parsed") if isinstance(result, dict) else result
        print(f"  ✅ 成功! 解析结果: {parsed}")
        return True
    except Exception as e:
        print(f"  ❌ 失败: {e}")
        return False


# ================================================================
# Step 5: 如果 Step3 失败 — 用 file_uri 但不用 structured_output，手动解析 JSON
# ================================================================
def step5_langchain_file_uri_manual_json(file_uri: str, mime_type: str):
    """file_uri + 不用 structured_output，在 prompt 里要求返回 JSON，手动解析。"""
    from langchain_google_genai import ChatGoogleGenerativeAI
    from langchain_core.messages import HumanMessage, SystemMessage
    import json

    _divider("Step 5: LangChain + file_uri + 手动 JSON（无 structured_output）")
    print(f"  file_uri: {repr(file_uri)}")

    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0)

    messages = [
        SystemMessage(content="你是音频分析助手。请严格以 JSON 格式返回结果，不要包含其他文本。"),
        HumanMessage(content=[
            {"type": "text", "text": '分析这段音频，以 JSON 返回：{"description": "...", "language": "...", "duration_estimate": "..."}'},
            {"type": "media", "file_uri": file_uri, "mime_type": mime_type},
        ]),
    ]

    try:
        response = llm.invoke(messages)
        text = response.content if hasattr(response, "content") else str(response)
        print(f"  ✅ LLM 回复: {repr(text[:300])}")
        # 尝试解析 JSON
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0]
        parsed = json.loads(clean)
        print(f"  ✅ JSON 解析成功: {parsed}")
        return True
    except Exception as e:
        print(f"  ❌ 失败: {e}")
        return False


# ================================================================
# Step 6: 尝试不同的 uri 格式（如果 step2/3 失败）
# ================================================================
def step6_try_uri_variants(original_uri: str, mime_type: str):
    """尝试不同格式的 file_uri 来定位哪个格式能用。"""
    _divider("Step 6: 尝试不同 uri 格式变体")

    variants = {}

    # 原始 uri
    variants["原始 (uploaded_file.uri)"] = original_uri

    # 如果包含 v1beta，去掉 v1beta
    if "v1beta" in original_uri:
        no_v1beta = original_uri.replace("/v1beta/files/", "/files/")
        variants["去掉 v1beta"] = no_v1beta

    # 短格式 files/xxx
    if "/files/" in original_uri:
        file_id = original_uri.split("/files/")[-1].strip("/").split("?")[0]
        variants["短格式 files/xxx"] = f"files/{file_id}"
        # 标准完整 URL（无 v1beta）
        full_url = f"https://generativelanguage.googleapis.com/files/{file_id}"
        variants["标准完整 URL"] = full_url

    print(f"  共 {len(variants)} 种变体:")
    results = {}
    for label, uri in variants.items():
        print(f"\n  --- {label}: {repr(uri)} ---")
        ok = step2_langchain_file_uri_plain(uri, mime_type)
        results[label] = ok

    print(f"\n  === 变体结果汇总 ===")
    for label, ok in results.items():
        print(f"    {'✅' if ok else '❌'} {label}: {variants[label]}")
    return results


# ================================================================
# Main
# ================================================================
def main():
    _divider("Gemini 音频 file_uri + LangChain 组合测试")
    print("  测试目标: 定位 400 错误的根因")
    print("  关键路径: ChatGoogleGenerativeAI → with_structured_output → file_uri")
    print("  对照参考: video evaluator 用同样的路径成功调用过")

    audio_path = _find_test_audio()
    if not audio_path:
        print("  ❌ 没有找到测试音频文件，请确认 tests/fixtures/ 下有 test_under_70mb.wav")
        return

    # Step 1: 上传
    client, uploaded = step1_upload(audio_path)
    if uploaded is None:
        return

    file_uri = uploaded.uri
    mime_type = getattr(uploaded, "mime_type", None) or "audio/wav"

    # Step 2: LangChain + file_uri（无 structured_output）
    s2_ok = step2_langchain_file_uri_plain(file_uri, mime_type)

    # Step 3: LangChain + file_uri + with_structured_output
    s3_ok = step3_langchain_file_uri_structured(file_uri, mime_type)

    # Step 4: LangChain + inline base64 + with_structured_output（对照组）
    s4_ok = step4_langchain_inline_structured(audio_path)

    # Step 5: 仅在 Step 3 失败时运行
    s5_ok = None
    if not s3_ok:
        s5_ok = step5_langchain_file_uri_manual_json(file_uri, mime_type)

    # Step 6: 如果 Step 2 或 Step 3 失败，尝试不同 uri 格式
    s6_results = None
    if not s2_ok or not s3_ok:
        s6_results = step6_try_uri_variants(file_uri, mime_type)

    # ========== 总结 ==========
    _divider("测试结果总结")
    print(f"  uploaded_file.uri 格式: {repr(file_uri)}")
    print()
    print(f"  Step 2 (file_uri 无 structured): {'✅ 成功' if s2_ok else '❌ 失败'}")
    print(f"  Step 3 (file_uri + structured):   {'✅ 成功' if s3_ok else '❌ 失败'}")
    print(f"  Step 4 (inline + structured):     {'✅ 成功' if s4_ok else '❌ 失败'}")
    if s5_ok is not None:
        print(f"  Step 5 (file_uri + 手动 JSON):   {'✅ 成功' if s5_ok else '❌ 失败'}")

    print()
    if s2_ok and s3_ok:
        print("  📌 结论: file_uri + with_structured_output 没问题！")
        print("     生产代码的 400 错误可能是其他原因（MIME类型、prompt模板等）。")
    elif s2_ok and not s3_ok and s4_ok:
        print("  📌 结论: file_uri + with_structured_output 组合有冲突！")
        print("     但 file_uri 本身 OK，structured_output 本身也 OK。")
        print("     修复方案: 大文件走 file_uri 时不用 structured_output，改为手动解析 JSON。")
        if s5_ok:
            print("     ✅ Step 5 验证了手动 JSON 方案可行。")
    elif not s2_ok:
        print("  📌 结论: file_uri 本身就有问题（和 structured_output 无关）。")
        print("     请检查 Step 6 的 uri 格式变体结果。")
        if s6_results:
            working = [k for k, v in s6_results.items() if v]
            if working:
                print(f"     可用格式: {working}")
    elif s2_ok and not s3_ok and not s4_ok:
        print("  📌 结论: structured_output 本身就有问题（和 file_uri 无关）。")
    else:
        print("  📌 需要进一步排查。")


if __name__ == "__main__":
    main()
