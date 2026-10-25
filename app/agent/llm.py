"""
大模型初始化模块

负责从 .env 中读取模型配置，并创建项目统一复用的模型对象
后续主智能体和子智能体都从这里导入 model，避免在多个文件里重复加载环境变量
"""

import os

from langchain.chat_models import init_chat_model
from app.local_config import get_settings

# Read explicit application configuration.
get_settings()

# 使用 OpenAI 兼容协议接入模型；具体模型名由 .env 中的 LLM_QWEN_MAX 控制
model = init_chat_model(
    model=os.getenv("LLM_QWEN_MAX"),
    model_provider="openai",
    timeout=45,
    max_retries=0,
    max_tokens=2048,
)
