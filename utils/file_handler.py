import os,hashlib
import sys

# 直接运行 `python utils/file_handler.py` 时，sys.path[0] 是 utils 目录，
# 项目根目录不在其中，`from utils.xxx import` 会报 ModuleNotFoundError。
# 因此必须把下面这行放在所有 `utils.*` 导入之前。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from utils.logger_handler import logger
from langchain_core.documents import Document
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_community.document_loaders import Docx2txtLoader




def get_file_md5_hex(filepath):   #获取文件的md5十六进制字符串
    if not os.path.exists(filepath):   #判断文件是否存在
        logger.error(f"[md5计算]文件不存在：{filepath}")
        return None
    if not os.path.isfile(filepath):   #判断是否是文件
        logger.error(f"[md5计算]不是文件：{filepath}")
        return

    md5_obj=hashlib.md5()

    chunk_size=4096   #每次读取文件数据的大小 4kb分片，避免文件过大爆内存
    try:
         with open(filepath, "rb") as f:
              while chunk:=f.read(chunk_size):
                 md5_obj.update(chunk)
              md5_hex=md5_obj.hexdigest()
              return md5_hex
    except Exception as e:   #异常处理
         logger.error(f"[md5计算]文件读取异常：{filepath},{str(e)}")
         return None


def listdir_with_allowed_type(path: str, allowed_types: tuple[str, ...]) -> tuple[str, ...]:
    """返回目录内指定后缀的文件；目录不合法时必须返回空，而不是返回后缀常量。"""
    files: list[str] = []
    if not os.path.isdir(path):
        logger.error(f"[listdir_with_allowed_type]{path}不是文件夹")
        return tuple()

    normalized_types = tuple(f".{item.lower().lstrip('.')}" for item in allowed_types)
    for name in sorted(os.listdir(path)):
        full_path = os.path.join(path, name)
        if os.path.isfile(full_path) and name.lower().endswith(normalized_types):
            files.append(full_path)
    return tuple(files)

def pdf_loader(filepath: str, passwd=None):
    return PyPDFLoader(filepath, passwd).load()


def txt_loader(filepath: str):
    return TextLoader(filepath, encoding="utf-8").load()


def docx_loader(filepath: str) -> list[Document]:
    return Docx2txtLoader(filepath).load()
