"""
Unit tests for Story Tools
"""
import pytest
import json
from unittest.mock import AsyncMock, patch

# 导入核心实现函数（不包含 @tool 装饰器）
from app.tools.new.story_tools import _generate_story_with_llm_impl
from app.models.image_result import StoryProvider


# 标记为单元测试
pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_story_generation_success():
    """测试故事生成成功的情况"""
    
    # Mock LLM响应
    mock_response = AsyncMock()
    mock_response.content = """
从前有一个小村庄，村里住着一个善良的老人。他每天都会帮助村民，从不求回报。

有一天，村里来了一个陌生人，他看起来很疲惫。老人主动邀请他到家里休息，并为他准备了热腾腾的饭菜。

陌生人被老人的善良感动，原来他是国王派来的使者，专门寻找最善良的人。最终，老人被选为国王的顾问，他的善良传遍了整个王国。

这个故事告诉我们，善良是最宝贵的品质，它不仅能帮助别人，也能为自己带来好运。
"""
    
    with patch('app.tools.new.story_tools.get_model_service') as mock_service:
        # Mock模型服务
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        
        mock_model_service = AsyncMock()
        mock_model_service.llm = mock_llm
        
        mock_service.return_value = mock_model_service
        
        # 测试参数
        test_prompt = "创作一个关于善良的故事"
        
        # 调用核心实现函数
        result = await _generate_story_with_llm_impl(prompt=test_prompt)
        
        # 解析结果
        result_data = json.loads(result)
        
        # 验证结果
        assert result_data['success'] is True
        assert result_data['provider'] == StoryProvider.GPT
        assert result_data['story_content'] is not None
        assert len(result_data['story_content']) > 0
        assert result_data['generated_prompt'] == test_prompt
        assert result_data['message'] == "故事生成成功"


@pytest.mark.asyncio
async def test_story_generation_error_handling():
    """测试故事生成错误处理"""
    
    with patch('app.tools.new.story_tools.get_model_service') as mock_service:
        # Mock模型服务抛出异常
        mock_service.side_effect = Exception("模型服务不可用")
        
        # 调用核心实现函数
        result = await _generate_story_with_llm_impl(prompt="创作一个故事")
        
        # 解析结果
        result_data = json.loads(result)
        
        # 验证错误处理
        assert result_data['success'] is False
        assert result_data['provider'] == StoryProvider.GPT
        assert result_data['error_message'] is not None
        assert "模型服务不可用" in result_data['error_message']


@pytest.mark.asyncio
async def test_story_generation_with_empty_prompt():
    """测试空提示词的处理"""
    
    # Mock LLM响应
    mock_response = AsyncMock()
    mock_response.content = "这是一个默认故事..."
    
    with patch('app.tools.new.story_tools.get_model_service') as mock_service:
        # Mock模型服务
        mock_llm = AsyncMock()
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)
        
        mock_model_service = AsyncMock()
        mock_model_service.llm = mock_llm
        
        mock_service.return_value = mock_model_service
        
        # 测试空提示词
        result = await _generate_story_with_llm_impl(prompt="")
        
        result_data = json.loads(result)
        assert result_data['success'] is True
        assert result_data['generated_prompt'] == ""

