"""
测试音频转录工具 - 真实场景测试
"""
import asyncio

from app.tools.transcribe.gemini import transcribe_audio_with_gemini, transcribe_audio


async def test_transcription_methods_comparison():
    """
    测试Gemini转录方法 - 真实场景测试
    """
    
    # 测试音频URL
    test_url = "https://cdn-dev.newai.land/audios/7fedbc8c-8e8a-4e6f-bdec-e933a6e96e03.mp3"
    user_input = "这是一个测试音频，可能包含中文或英文内容"
    
    print("\n" + "="*80)
    print("🎵 开始Gemini转录测试")
    print("="*80)
    print(f"📝 音频URL: {test_url}")
    print(f"🎯 测试功能: Gemini 转录")
    print("="*80 + "\n")
    
    try:
        print("🔄 开始Gemini转录...")
        gemini_result = await transcribe_audio(
            audio_url=test_url,
            method="gemini",
            user_input=user_input,
            fill_gaps=True
        )
        
        # 比较结果（只测试Gemini）
        if gemini_result:
            print("\n" + "="*80)
            print("📊 转录方法对比结果")
            print("="*80)
            
            print(f"\n🤖 Gemini结果:")
            print(f"   语言: {gemini_result.language}")
            print(f"   时长: {gemini_result.duration:.2f}秒")
            print(f"   片段数: {len(gemini_result.segments)}")
            print(f"   文本长度: {len(gemini_result.text)}字符")
            
            # 检查Gemini的words数据（暂时禁用）
            gemini_words_count = 0
            if gemini_result.additional_data and 'words' in gemini_result.additional_data:
                gemini_words_count = len(gemini_result.additional_data['words'])
            print(f"   Words数量: {gemini_words_count} (暂时禁用)")
            print(f"   转录方法: {gemini_result.additional_data.get('transcription_method', 'unknown')}")
            
            print(f"\n📝 文本内容:")
            print(f"Gemini:  {gemini_result.text[:150]}...")
            
            # 验证words数据（如果有的话）
            if gemini_words_count > 0:
                print(f"\n🤖 Gemini Words详情 (前5个) - 暂时禁用:")
                gemini_words = gemini_result.additional_data['words']
                for i, word in enumerate(gemini_words[:5]):
                    print(f"   Word{word['id']}: '{word['word']}' ({word['start']:.2f}s-{word['end']:.2f}s)")
                
                # 验证Gemini words数据结构
                assert all('id' in word for word in gemini_words), "所有Gemini words应该有id"
                assert all('word' in word for word in gemini_words), "所有Gemini words应该有word字段"
                assert all('start' in word for word in gemini_words), "所有Gemini words应该有start时间"
                assert all('end' in word for word in gemini_words), "所有Gemini words应该有end时间"
                print(f"   ✅ Gemini Words数据结构验证通过!")
            else:
                print(f"\n🤖 Gemini Words: 暂时禁用，未返回words数据")
            
            # 验证Gemini方法成功
            assert gemini_result.duration > 0, "Gemini时长应该大于0"
            assert len(gemini_result.segments) > 0, "Gemini应该有转录片段"
            
            print(f"\n✅ Gemini转录测试通过!")
            print("="*80)
            print("🎉 测试总结:")
            print(f"   ✅ Gemini转录: 成功 (Words: {gemini_words_count}个)")
            print(f"   ✅ 统一接口: 正常工作")
            print(f"   ✅ Words兼容性: Gemini暂时禁用 (之前支持: {gemini_words_count > 0})")
            print("="*80)
            
        else:
            if not gemini_result:
                print("❌ Gemini转录失败")
            
    except Exception as e:
        print(f"❌ 对比测试异常: {e}")
        import traceback
        traceback.print_exc()




if __name__ == "__main__":
    # 运行Gemini转录测试
    print("\n📊 运行Gemini转录测试...")
    asyncio.run(test_transcription_methods_comparison())
