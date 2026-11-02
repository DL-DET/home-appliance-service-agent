import os
import sys

# 直接运行 `python rag/vector_store.py` 时，sys.path[0] 是 rag 目录，
# 项目根目录不在其中，`from utils.xxx import`、`from model.xxx import`
# 会报 ModuleNotFoundError: No module named 'utils'。
# 因此必须在所有项目内包的导入之前，把项目根目录加入 sys.path。
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from langchain_chroma import Chroma
from utils.config_handler import chroma_conf
from model.factory import get_embedding_model
from langchain_text_splitters import RecursiveCharacterTextSplitter
from utils.path_tool import get_abs_path
from utils.file_handler import pdf_loader,txt_loader,docx_loader,listdir_with_allowed_type,get_file_md5_hex
from utils.logger_handler import logger
from langchain_core.documents import Document




class VectorStoreService:
    def __init__(self):
        self.vector_store =Chroma(
            collection_name=chroma_conf["collection_name"],
            embedding_function=get_embedding_model(),
            persist_directory=get_abs_path(chroma_conf["persist_directory"])

        )
        self.spliter= RecursiveCharacterTextSplitter(
            chunk_size=chroma_conf["chunk_size"],
            chunk_overlap=chroma_conf["chunk_overlap"],
            separators=chroma_conf["separators"],
            length_function=len
        )


    def get_retriever(self):
        return self.vector_store.as_retriever(search_kwargs={"k":chroma_conf["k"]})


    def load_documents(self):
        """
        从数据文件夹内读取数据文件，转为向量存入向量库
        要计算文件的MD5做去重
         :return:None
        """
        def check_md5_hex(md5_for_check: str) -> bool:
            if not os.path.exists(get_abs_path(chroma_conf["md5_hex_store"])):
                return False
            with open(get_abs_path(chroma_conf["md5_hex_store"]),"r",encoding="utf-8") as f:
                for line in f.readlines():
                    line=line.strip()
                    if line==md5_for_check:
                        return True  #md5处理过
                return False

        def save_md5_hex(md5_for_check: str) -> None:
            md5_path = get_abs_path(chroma_conf["md5_hex_store"])
            os.makedirs(os.path.dirname(md5_path), exist_ok=True)
            with open(md5_path, "a", encoding="utf-8") as f:
                f.write(md5_for_check+"\n")


        def get_file_documents(read_path:str):
            suffix = os.path.splitext(read_path)[1].lower()
            if suffix == ".txt":
                return txt_loader(read_path)
            if suffix == ".pdf":
                return pdf_loader(read_path)
            if suffix == ".docx":
                return docx_loader(read_path)
            return []

        allowed_files_path :list[str] = listdir_with_allowed_type(
            get_abs_path(chroma_conf["data_path"]),
            tuple(chroma_conf["allow_knowledge_file_type"])
            )
        for path in allowed_files_path:
            #获取md5
            md5_hex=get_file_md5_hex(path)
            if not md5_hex:
                logger.warning(f"[加载知识库] 无法计算文件MD5，跳过：{path}")
                continue
            if check_md5_hex(md5_hex):
                logger.info(f"[加载知识库] {path}内容已经存在知识库内 跳过")
                continue
            try:
                documents: list[Document]=get_file_documents(path)
                if not documents:
                    logger.info(f"[加载知识库] {path}内容为空  跳过")
                    continue
                split_document:list[Document]=self.spliter.split_documents(documents)

                if not split_document:
                    logger.info(f"[加载知识库] {path}分片后没有有效内容  跳过")
                    continue
                 #添加向量库中
                self.vector_store.add_documents(split_document)
                      #记录这个已经处理好的文件的md5，避免下次重复加载
                save_md5_hex(md5_hex)


                logger.info(f"[加载知识库] {path}内容处理完毕")
            except Exception as e:
                #exc_info为True会记录详细的报错堆栈，如果为False则只记录错误信息
                logger.error(f"[加载知识库] {path}处理异常：{str(e)}",exc_info=True)
                continue


if __name__ == "__main__":
    vs=VectorStoreService()
    vs.load_documents()

    retriever=vs.get_retriever()
    res=retriever.invoke("宠物")
    for r in res:
        print(r.page_content)
        print("="*20)
