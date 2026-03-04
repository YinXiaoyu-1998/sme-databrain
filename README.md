# SME Data Brain (FastAPI)

Excel-first RAG + structured-query service with LLM tool calling.

## Architecture

The service uses an **agent-driven** approach: the LLM receives a lightweight data catalog (sheet names, columns, types) and decides which tools to call to answer each question.

**Available tools:**
- `query_data` -- structured SQL queries against Excel row data (SheetRow table)
- `vector_search` -- semantic similarity search over text chunks (fallback for exploratory questions)
- `generate_chart` -- bar/line/pie chart generation via matplotlib
- `generate_csv` -- CSV file export

## Commands

Install dependencies (first time):

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the project locally:

```bash
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Purge all uploaded and generated files (disk + database):

```bash
bash migration/purge_all_files.sh
```

## Environment Variables

- `GOOGLE_API_KEY`: Gemini API key
- `DATABASE_URL`: PostgreSQL connection string
- `EMBEDDING_MODEL`: defaults to `all-MiniLM-L6-v2`
- `MAX_EXCEL_FILE_MB`: defaults to `25`
- `MAX_ROWS_PER_SHEET`: defaults to `10000`
- `EXCEL_CHUNK_SIZE`: defaults to `40`
- `EXCEL_CHUNK_OVERLAP`: defaults to `8`
- `GENERATED_FILES_DIR`: directory for LLM-generated charts and data files (default: `/Users/xiaoyuyin/Desktop/YXY_DEV/SME/sme-backend/generats`)

## API

### POST `/context/load`

Synchronous file ingestion. Parses Excel into:
1. Text chunks + vector embeddings (for `vector_search`)
2. Structured SheetMeta + SheetRow records (for `query_data`)

Request body:

```json
{
  "fileId": "backend-datafile-id",
  "filepath": "/absolute/path/to/file.xlsx",
  "mimeType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
}
```

### POST `/chat`

Agent-driven chat. The LLM receives a data catalog and decides which tools to call.

Request body:

```json
{
  "userId": "backend-user-id",
  "fileId": "optional-backend-datafile-id",
  "chatId": "optional-chat-session-id",
  "message": "这个月销量最好的产品是什么？",
  "history": []
}
```

Response body:

```json
{
  "answer": "根据数据分析...",
  "generatedFiles": [
    {
      "id": "uuid",
      "fileType": "chart",
      "mimeType": "image/png",
      "filename": "uuid_销售分析图.png",
      "path": "/path/to/file.png",
      "size": 12345
    }
  ]
}
```

## Database Tables

### Managed by this service (raw SQL):
- `SheetMeta` -- per-sheet metadata catalog (columns, types, row counts)
- `SheetRow` -- structured row data as JSONB
- `GeneratedFile` -- LLM-generated file records

### Managed by sme-backend (Prisma):
- `DataFile`, `Document`, `Chunk`, `Embedding`, `RAGIngestionRun`
