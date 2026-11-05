"""
为整个工程提供统一的绝对路径

"""
import os
#获取工程所在的根目录
#：return：字符串根目录
def get_project_root():
    current_file= os.path.abspath(__file__)  #当前文件的的绝对路径
#获取工程根目录，先获取文件所在文件夹的绝对路径
    current_dir=os.path.dirname(current_file)
#获取工程根目录
    project_root=os.path.dirname(current_dir)
    return project_root



def get_abs_path(relative_path:str)->str:
    """
    传递相对路径，得到绝对路径
    :param relative_path: 相对路径
    :return: 绝对路径
    """
    project_root=get_project_root()
    return os.path.join(project_root,relative_path)


def get_basename(path: str) -> str:
    """兼容 Windows 反斜杠和 POSIX 斜杠，只返回文件名。"""
    text = str(path or "").strip().replace("\\", "/").rstrip("/")
    return text.rsplit("/", 1)[-1] if text else ""

if __name__=="__main__":
    print(get_abs_path("config/config.txt"))
