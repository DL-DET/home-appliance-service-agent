import logging
import os
import sys
from datetime import datetime

try:
    from utils.path_tool import get_abs_path
except ModuleNotFoundError as exc:
    # 直接 python utils/logger_handler.py 运行时，项目根目录不在 sys.path 里
    if exc.name != "utils":
        raise
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from utils.path_tool import get_abs_path

#日志保存根目录

LOG_ROOT = get_abs_path("logs")

#确保日志的目录存在
os.makedirs(LOG_ROOT,exist_ok=True)

#日志格式
DEFAULT_LOG_FORMAT=logging.Formatter('%(asctime)s - %(name)s - %(levelname)s -%(filename)s:%(lineno)d -%(message)s')

def get_logger(
        name:str="agent",
        console_level:int=logging.INFO,
        file_level:int=logging.DEBUG,
        log_file =None,

) ->logging.Logger:
    logger=logging.getLogger(name)
    logger.setLevel(logging.DEBUG)
#避免重复添加Handler
    if  logger.handlers:
        return logger
    #控制台Handler
    console_handel=logging.StreamHandler()
    console_handel.setLevel(console_level)
    console_handel.setFormatter(DEFAULT_LOG_FORMAT)

    logger.addHandler(console_handel)

    #文件Handler
    if log_file is None:
        log_file=os.path.join(LOG_ROOT,f"{name}_{datetime.now().strftime('%Y%m%d')}.log")

    file_handel=logging.FileHandler(log_file,encoding="utf-8")
    file_handel.setLevel(file_level)
    file_handel.setFormatter(DEFAULT_LOG_FORMAT)
    logger.addHandler(file_handel)
    return logger



#快捷获取日志器
logger=get_logger()



if __name__=="__main__":
    logger.debug("调试日志")
    logger.info("信息日志")
    logger.warning("警告日志")
    logger.error("错误日志")
