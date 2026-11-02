from abc import ABC, abstractmethod
from functools import lru_cache
import os
from pathlib import Path
from typing import Optional

import sys
from dotenv import load_dotenv

# 直接运行 `python model/factory.py` 时，sys.path[0] 是 model 目录，
# 项目根目录不在其中，`from utils.xxx import` 会报 ModuleNotFoundError。
# 因此必须在所有项目内包的导入之前，把项目根目录加入 sys.path。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_community.chat_models.tongyi import ChatTongyi
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from utils.config_handler import rag_conf


# 默认的 load_dotenv() 会向父目录搜索，可能误读其他项目的密钥。
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def has_dashscope_api_key() -> bool:
    """用于界面提前提示配置问题，避免首次对话时才报错。"""
    return bool(os.getenv("DASHSCOPE_API_KEY", "").strip())


def _require_dashscope_api_key() -> None:
    if not has_dashscope_api_key():
        raise RuntimeError(
            "缺少 DASHSCOPE_API_KEY，请配置项目根目录的 .env 或 Streamlit secrets。"
        )





class BaseModelFactory(ABC):
    @abstractmethod
    def generator(self) ->Optional[Embeddings|BaseChatModel]:
        pass


class ChatModelFactory(BaseModelFactory):
    def generator(self) -> Optional[BaseChatModel]:
        return ChatTongyi(
            model=rag_conf["chat_model_name"],
            max_retries=int(rag_conf["chat_max_retries"]),
            # DashScope 的 timeout 是流式请求两次数据之间的空闲超时。
            # 不设置时，服务端长时间不返回 token 会让页面无限等待。
            model_kwargs={"timeout": int(rag_conf["chat_timeout_seconds"])},
        )

class EmbeddingModelFactory(BaseModelFactory):
    def generator(self) ->Optional[Embeddings]:
        return DashScopeEmbeddings(model=rag_conf["embedding_model_name"])


@lru_cache(maxsize=1)
def get_chat_model() -> BaseChatModel:
    """只有真正调用模型时才实例化，测试和页面启动不需要 API Key。"""
    _require_dashscope_api_key()
    return ChatModelFactory().generator()


@lru_cache(maxsize=1)
def get_embedding_model() -> Embeddings:
    _require_dashscope_api_key()
    return EmbeddingModelFactory().generator()
