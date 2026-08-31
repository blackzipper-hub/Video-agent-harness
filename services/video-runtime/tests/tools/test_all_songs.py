"""
一口气测试所有本地歌曲 - 使用 Gemini 转录

运行方式：
python tests/tools/test_all_songs.py
"""
import os
from dotenv import load_dotenv

# 加载环境变量
environment = os.getenv("ENVIRONMENT", "development")
if environment == "development":
    load_dotenv(".env.development")
    print("✅ 已加载环境变量: .env.development")
elif environment == "production":
    load_dotenv(".env.production")
    print("✅ 已加载环境变量: .env.production")
else:
    load_dotenv(".env.development")
    print("✅ 已加载环境变量: .env.development")

import asyncio
import sys
from pathlib import Path

from app.tools.transcribe.gemini import transcribe_audio_with_gemini

# 测试歌曲 URL 列表（已上传到 S3）
TEST_SONGS = [
    {
        "name": "1-en-blackpink kill this love-20s.mp3",
        "url": "https://cdn-dev.newai.land/audios/test_song_f2ab506a-5d7c-49a6-a745-ed1d27e2f29c.mp3"
    },
    {
        "name": "2-en-City of Stars-50s.MP3",
        "url": "https://cdn-dev.newai.land/audios/test_song_2a1fafc4-85b8-4916-9da7-fd7661ceb88b.mp3"
    },
    {
        "name": "3-en-vidmuse-song-1-1min.MP3",
        "url": "https://cdn-dev.newai.land/audios/test_song_2de93734-52ba-47c9-a7bf-df36d3ab38fa.mp3"
    },
    {
        "name": "4-小幸运-24s.MP3",
        "url": "https://cdn-dev.newai.land/audios/test_song_e73273da-82d3-43ed-89e2-eb725243a2ff.mp3"
    },
    {
        "name": "5-慢热小悠成长主题曲（前奏旋律轻快，带点慵懒的合成器音效）-4min.mp3",
        "url": "https://cdn-dev.newai.land/audios/test_song_acd9da89-3dc8-48a5-887e-f653a43741d9.mp3"
    },
    {
        "name": "6-漠河舞厅-35s.MP3",
        "url": "https://cdn-dev.newai.land/audios/test_song_20dfd611-650e-4c7a-8828-1bb91624f6cd.mp3"
    },
    {
        "name": "7-许嵩-suno-30s.MP3",
        "url": "https://cdn-dev.newai.land/audios/test_song_188d6615-0622-459b-907f-a0731e147f06.mp3"
    },
]


async def transcribe_song(song_info: dict, index: int, total: int):
    """转录单个歌曲（使用 URL）"""
    filename = song_info['name']
    cdn_url = song_info['url']
    
    print("\n" + "="*80)
    print(f"🎵 [{index}/{total}] {filename}")
    print("="*80)
    print(f"📝 URL: {cdn_url}")
    
    try:
        # 使用 Gemini 转录
        print(f"🔄 使用 Gemini 转录...")
        result = await transcribe_audio_with_gemini(
            audio_url=cdn_url,
            user_input="这是一首歌曲，请转录出歌词",
            fill_gaps=False
        )
        
        if result:
            words_count = 0
            if result.additional_data and 'words' in result.additional_data:
                words_count = len(result.additional_data['words'])
            
            print(f"\n✅ 转录成功!")
            print(f"   语言: {result.language}")
            print(f"   时长: {result.duration:.2f}秒")
            print(f"   片段数: {len(result.segments)}")
            print(f"   文本长度: {len(result.text)}字符")
            print(f"   Words数量: {words_count}")
            
            print(f"\n📝 歌词预览:")
            print("-" * 80)
            # 显示前200个字符
            preview = result.text[:200]
            if len(result.text) > 200:
                preview += "..."
            print(preview)
            print("-" * 80)
            
            return {
                'filename': filename,
                'success': True,
                'language': result.language,
                'duration': result.duration,
                'segments': len(result.segments),
                'text_length': len(result.text),
                'words_count': words_count,
                'full_text': result.text,
                'cdn_url': cdn_url
            }
        else:
            print("❌ 转录失败")
            return {
                'filename': filename,
                'success': False,
                'error': '转录返回空结果'
            }
            
    except Exception as e:
        print(f"❌ 处理失败: {e}")
        import traceback
        traceback.print_exc()
        return {
            'filename': filename,
            'success': False,
            'error': str(e)
        }


async def main():
    print("\n" + "="*80)
    print("🎵 批量测试所有歌曲 - Gemini 转录")
    print("="*80)
    print(f"📋 共 {len(TEST_SONGS)} 首歌曲")
    print("="*80)
    
    results = []
    
    # 逐个处理每首歌
    for i, song_info in enumerate(TEST_SONGS, 1):
        result = await transcribe_song(song_info, i, len(TEST_SONGS))
        if result:
            results.append(result)
        
        # 每首歌之间稍微休息一下
        if i < len(TEST_SONGS):
            await asyncio.sleep(2)
    
    # 打印汇总报告
    print("\n\n" + "="*80)
    print("📊 测试汇总报告")
    print("="*80)
    
    success_count = sum(1 for r in results if r.get('success'))
    fail_count = len(results) - success_count
    
    print(f"\n✅ 成功: {success_count}/{len(results)}")
    print(f"❌ 失败: {fail_count}/{len(results)}")
    
    if success_count > 0:
        print("\n📋 成功的歌曲:")
        print("-" * 80)
        for r in results:
            if r.get('success'):
                print(f"  ✅ {r['filename']}")
                print(f"     语言: {r['language']} | 时长: {r['duration']:.1f}s | "
                      f"片段: {r['segments']} | 文本: {r['text_length']}字符 | "
                      f"Words: {r['words_count']}")
    
    if fail_count > 0:
        print("\n❌ 失败的歌曲:")
        print("-" * 80)
        for r in results:
            if not r.get('success'):
                print(f"  ❌ {r['filename']}: {r.get('error', 'Unknown error')}")
    
    # 保存详细结果到文件
    output_file = "test_results_all_songs_gemini.txt"
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write("="*80 + "\n")
        f.write("🎵 所有歌曲 Gemini 转录详细结果\n")
        f.write("="*80 + "\n\n")
        
        for i, r in enumerate(results, 1):
            f.write(f"\n{'='*80}\n")
            f.write(f"[{i}/{len(results)}] {r['filename']}\n")
            f.write(f"{'='*80}\n")
            
            if r.get('success'):
                f.write(f"✅ 转录成功\n")
                f.write(f"语言: {r['language']}\n")
                f.write(f"时长: {r['duration']:.2f}秒\n")
                f.write(f"片段数: {r['segments']}\n")
                f.write(f"文本长度: {r['text_length']}字符\n")
                f.write(f"Words数量: {r['words_count']}\n")
                f.write(f"CDN URL: {r['cdn_url']}\n")
                f.write(f"\n完整歌词:\n")
                f.write("-" * 80 + "\n")
                f.write(r['full_text'])
                f.write("\n" + "-" * 80 + "\n")
            else:
                f.write(f"❌ 转录失败: {r.get('error')}\n")
    
    print(f"\n📝 详细结果已保存到: {output_file}")
    print("="*80)
    print(f"\n✅ 测试完成! 成功率: {success_count}/{len(results)} ({success_count*100//len(results) if len(results) > 0 else 0}%)")


if __name__ == "__main__":
    asyncio.run(main())
