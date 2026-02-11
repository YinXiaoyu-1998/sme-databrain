
import os
from typing import Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import pandas as pd
import re
# LangChain related library
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_experimental.agents.agent_toolkits import create_pandas_dataframe_agent
from langchain_classic.chains import create_retrieval_chain
from langchain_classic.chains.combine_documents import create_stuff_documents_chain

from langchain_core.prompts import ChatPromptTemplate
from dotenv import load_dotenv

app = FastAPI(title="SME Data Brain", version="0.0.1")

# ==========================================
# Configuration area
# ==========================================
# Load .env so os.getenv can see GEMINI_API_KEY
load_dotenv()
# Use environment variable to store the API key
# os.environ["GOOGLE_API_KEY"] = os.getenv("GEMINI_API_KEY")

# ==========================================
# 🚑 网络急救包 (新增部分)
# ==========================================
# 强制 Python 的请求走 VPN 代理
# 请根据你的实际情况修改端口号 (比如 7890 或 7897)
os.environ["http_proxy"] = "http://127.0.0.1:7897"
os.environ["https_proxy"] = "http://127.0.0.1:7897"

# Initialize LLM
llm = ChatGoogleGenerativeAI(
    # model="gemini-2.5-flash-lite", # gemini-2.5-flash-lite, gemini-3-pro-preview
    model="gemini-3-pro-preview",
    temperature=0,
    max_retries=2,
    transport="rest", # If you encounter SSL errors, try uncommenting this line
)

# ==========================================
# 🧠 Global brain status (MVP core)
# ==========================================
# load the file into memory for this MVP.
# data will be lost after restart, but it's acceptable for this MVP.
GLOBAL_CONTEXT = {
    "df": None,           # store Excel DataFrame
    "vector_store": None, # store PDF vector store (Chroma)
    "current_file": None  # record the current loaded file name
}

def custom_error_handler(error: Exception) -> str:
    error_str = str(error)
    
    # 模式匹配：匹配 "Could not parse LLM output: `" 之后的所有内容
    # (.*) 是捕获组
    # re.DOTALL 意思是让点号 (.) 也能匹配换行符，这对于长篇分析很重要
    match = re.search(r"Could not parse LLM output: `(.*)`", error_str, re.DOTALL)
    
    if match:
        # group(1) 拿到的就是反引号包裹的完整内容（无论里面有没有嵌套反引号）
        # strip("`") 是为了保险起见，去掉可能残留在末尾的包裹符号
        return match.group(1).strip("`")
    
    # 处理 Action 缺失的备用逻辑
    if "Invalid Format: Missing 'Action:'" in error_str:
        return "分析已完成，但格式稍有偏差。请尝试在 Prompt 中强调只输出结论。"
        
    return str(error)

# define the request body structure
class LoadContextRequest(BaseModel):
    filepath: str
    mimeType: str

class ChatRequest(BaseModel):
    message: str # 用户的问题

@app.get("/")
def read_root():
    return {"status": "SME DataBrain is running"}

@app.post("/context/load")
async def load_context(request: LoadContextRequest):
    """
    receive the file path from NestJS, and load it into memory.
    """
    file_path = request.filepath
    mime_type = request.mimeType
    
    # 1. check if the file exists
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found at {file_path}")

    try:
        # ==========================================
        # branch A: process Excel (for data analysis)
        # ==========================================
        if "spreadsheet" in mime_type or "excel" in mime_type:
            print(f"📊 Loading Excel: {file_path}")
            # read Excel to Pandas DataFrame
            df = pd.read_excel(file_path)

            # replace all NaN (empty values) with empty string ""
            # so that FastAPI can convert it into JSON normally
            df = df.fillna("") 
            # ================================

            # update the global context
            GLOBAL_CONTEXT["df"] = df
            GLOBAL_CONTEXT["vector_store"] = None # clear the previous PDF context
            GLOBAL_CONTEXT["current_file"] = "excel"
            
            # return the summary of the data (column names and first few rows)
            return {
                "message": "Excel loaded successfully",
                "type": "excel",
                "columns": df.columns.tolist(),
                "row_count": len(df),
                "preview": df.head(3).to_dict()
            }

        # ==========================================
        # branch B: process PDF (for RAG question answering)
        # ==========================================
        elif "pdf" in mime_type:
            print(f"📄 Loading PDF: {file_path}")
            
            # 1. read the PDF text
            loader = PyPDFLoader(file_path)
            pages = loader.load()
            
            # 2. split the text (Chunking)
            # key point of RAG: split the big book into small pieces, so that it's easier to retrieve.
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=500, # each chunk is 500 characters
                chunk_overlap=50 # overlap 50 characters, to prevent context断裂
            )
            splits = text_splitter.split_documents(pages)
            
            # 3. vectorize and store (Embeddings & Storage)
            # we use the local model (HuggingFace) here, which is completely free, and doesn't require OpenAI Key
            # *careful*: the first time running will automatically download the model (about 100MB), which might be slow.
            embedding_function = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
            
            # create a temporary memory vector store
            vector_store = Chroma.from_documents(
                documents=splits,
                embedding=embedding_function,
                collection_name="sme_collection" # randomly named
            )
            
            # update the global context
            GLOBAL_CONTEXT["vector_store"] = vector_store
            GLOBAL_CONTEXT["df"] = None
            GLOBAL_CONTEXT["current_file"] = "pdf"

            return {
                "message": "PDF loaded and indexed successfully",
                "type": "pdf",
                "chunks_count": len(splits)
            }

        else:
            raise HTTPException(status_code=400, detail="Unsupported file type")

    except Exception as e:
        print(f"❌ Error loading file: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))


# {
#   "message": "出 2025 年 9 月 26 号的销售数据，销量最好的产品是哪些？"
# }

@app.post("/chat")
async def chat(request: ChatRequest):
    """
    Core Chat Interface: based on the current context, answer the question intelligently
    """
    question = request.message
    current_type = GLOBAL_CONTEXT["current_file"]

    if not current_type:
        return {"answer": "🧠 大脑空空如也。请先在左侧上传一个文件。"}

    try:
        # === Scenario A: Excel data analysis ===
        if current_type == "excel":
            df = GLOBAL_CONTEXT["df"]
            my_instruction = """
            你是一个经验丰富的营销分析师，
            注意如下事项：
            0. 严格使用中文回答问题。
            1. 严格根据所提供的数据回答问题，如果提供的数据不能回答相关问题，请直说，或是要求提供相关数据，不要编造数据。
            2. 你的回答必须严格遵守格式：
                - 如果你要写代码，请使用 Action: python_repl_ast
                - 如果你已经有了分析结果，**必须**以 "Final Answer:" 开头，然后写你的分析。
            3. 不要只输出 Thought 而没有 “Action” 或 “Final Answer”。
            4. 除了回答领导的问题，根据提供的数据，你还需要附上自己觉得领导会感兴趣的其他问题，领导可能会做出选择并追问。
            领导从公司数据库中调取了如下信息，并问了如下问题：
            """


            # Create Pandas Agent
            # allow_dangerous_code=True is required, because we want to let AI write Python code to calculate data
            agent = create_pandas_dataframe_agent(
                llm, 
                df, 
                verbose=True, 
                allow_dangerous_code=True,
                prefix=my_instruction,
                agent_executor_kwargs={"handle_parsing_errors": custom_error_handler}
                # agent_executor_kwargs={"handle_parsing_errors": True}
            )
            # Execute analysis
            response = agent.invoke(question)
            return {"answer": response["output"]}

        # === Scenario B: PDF knowledge base question answering (RAG) ===
        elif current_type == "pdf":
            vector_store = GLOBAL_CONTEXT["vector_store"]
            retriever = vector_store.as_retriever(search_kwargs={"k": 3}) # find the most relevant 3 chunks

            # Define Prompt (tell AI its role)
            prompt = ChatPromptTemplate.from_template("""
            你是一个企业助手。请根据下面的上下文回答用户的问题。
            如果上下文中没有答案，就诚实地说不知道，不要编造。
            
            <context>
            {context}
            </context>

            用户问题: {input}
            """)

            # 构建 RAG 链 (检索 -> 注入 Prompt -> LLM)
            document_chain = create_stuff_documents_chain(llm, prompt)
            retrieval_chain = create_retrieval_chain(retriever, document_chain)

            # 执行问答
            response = retrieval_chain.invoke({"input": question})
            return {"answer": response["answer"]}

    except Exception as e:
        print(f"❌ AI Error: {str(e)}")
        return {"answer": f"抱歉，我思考时遇到了错误: {str(e)}"}





