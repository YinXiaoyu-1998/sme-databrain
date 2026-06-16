# SME Data Brain

这是 SME 小产品里的 AI 数据分析服务，负责把上传的 Excel 解析为可检索的数据结构，并基于 LLM 回答问题、生成图表和导出文件。

## 这个服务是做什么的

`sme-databrain` 是整个系统里的数据处理与大模型执行层。它接收 `sme-backend` 发来的入库和聊天请求，把表格转成结构化数据和语义检索素材，再通过工具调用完成业务分析回答。

## 主要功能

- Excel 入库，生成结构化 sheet 元数据和行数据
- 文本切块与 embedding 向量生成
- 基于工具调用的数据问答
- 面向结构化行数据的 SQL 风格查询
- 面向探索类问题的向量检索兜底
- 生成 PNG 图表
- 生成 CSV 导出文件
- 记录生成文件元数据

## 技术栈概览

- FastAPI
- Python 3.11+
- Pandas + OpenPyXL
- PostgreSQL + psycopg
- Sentence Transformers
- LangChain
- Gemini（通过 `langchain-google-genai`）
- Matplotlib

## 主要接口

### `POST /context/load`

把上传的 Excel 文件解析并写入结构化与语义检索层。

### `POST /chat`

执行一轮智能分析对话。模型会拿到轻量的数据目录，并自行选择 `query_data`、`vector_search`、`generate_chart`、`generate_csv` 等工具。

## 本地开发

创建虚拟环境并安装依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

启动服务：

```bash
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

默认本地地址：

```text
http://localhost:8000
```

## 环境变量

本地常见 `.env` 配置：

```bash
GOOGLE_API_KEY=your_gemini_key
DATABASE_URL=postgresql://admin:password123@localhost:5432/sme_db
EMBEDDING_MODEL=all-MiniLM-L6-v2
MAX_EXCEL_FILE_MB=25
MAX_ROWS_PER_SHEET=10000
EXCEL_CHUNK_SIZE=40
EXCEL_CHUNK_OVERLAP=8
GENERATED_FILES_DIR=/absolute/path/to/sme-backend/generats
```

## 常用命令

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
bash migration/purge_all_files.sh
```

## 维护的数据

这个服务会写入或维护与以下内容相关的数据：

- `SheetMeta`
- `SheetRow`
- `GeneratedFile`
- 入库阶段生成的语义分块与向量

## 与其他服务的关系

- 接收 `sme-backend` 发来的入库与聊天分析请求
- 把分析相关数据写入 PostgreSQL
- 把生成的图表和导出文件写到由 `sme-backend` 对外暴露的目录
- 浏览器不会直接调用这个服务
