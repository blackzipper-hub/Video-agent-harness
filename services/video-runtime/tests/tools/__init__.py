"""
Tools integration tests

This package contains integration tests for various AI tools including:
- Suno API music generation
- Pollo AI video generation  
- Nano Banana (Gemini) image generation

To run all tool tests:
    pytest tests/tools/ -v

To run specific tool tests:
    pytest tests/tools/test_suno_integration.py -v
    pytest tests/tools/test_pollo_integration.py -v
    pytest tests/tools/test_nano_banana_integration.py -v

Note: These are integration tests that make real API calls and may consume credits.
"""
