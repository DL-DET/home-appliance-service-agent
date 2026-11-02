import os
import sys

# 直接运行 `python utils/prompt_loader.py` 时，sys.path[0] 是 utils 目录，
# 项目根目录不在其中，`from utils.xxx import` 会报 ModuleNotFoundError。
# 因此必须把下面这行放在所有 `utils.*` 导入之前。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from utils.config_handler import prompts_conf
from utils.logger_handler import logger
from utils.path_tool import get_abs_path


def load_system_prompts():
    try:
        system_prompt_path=get_abs_path(prompts_conf["main_prompt_path"])
    except KeyError as e:
        logger.error(f"[load_system_prompts]在yaml配置项中没有main_prompt_path配置项")
        raise e
    try:
       with open(system_prompt_path,"r",encoding="utf-8") as file:
           return file.read()
    except Exception as e:
        logger.error(f"[load_system_prompts]解析系统提示词出错，{str(e)}")
        raise e



def load_rag_prompts():
    try:
        rag_prompt_path=get_abs_path(prompts_conf["rag_summarize_prompt_path"])
    except KeyError as e:
        logger.error(f"[load_rag_prompts]在yaml配置项中没有rag_summarize_prompt_path配置项")
        raise e
    try:
        with open(rag_prompt_path,"r",encoding="utf-8") as file:
            return file.read()
    except Exception as e:
        logger.error(f"[load_rag_prompts]解析RAG提示词出错,{str(e)}")
        raise e



def load_report_prompts():
    try:
        report_prompt_path=get_abs_path(prompts_conf["report_prompt_path"])
    except KeyError as e:
        logger.error(f"[load_report_prompts]在yaml配置项中没有report_prompt_path配置项")
        raise e
    try:
        with open(report_prompt_path,"r",encoding="utf-8") as file:
            return file.read()
    except Exception as e:
        logger.error(f"[load_report_prompts]解析报告生成提示词出错，{str(e)}")
        raise e


if __name__ == '__main__':
    print(load_system_prompts())
    print(load_rag_prompts())
    print(load_report_prompts())
