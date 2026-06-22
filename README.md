# SME Data Brain

AI analysis service for the SME product. It ingests uploaded Excel files, builds structured and semantic retrieval layers, answers business questions, and generates charts or CSV outputs.

## What This Service Does

`sme-databrain` is the data and LLM execution layer of the system. It receives ingestion requests from `sme-backend`, turns spreadsheets into queryable structures, and uses tool-calling to answer natural-language questions with text, charts, and exports.

## Core Features

- Excel ingestion into structured sheet metadata and row records
- Text chunking and embedding generation for semantic retrieval
- Tool-based question answering over uploaded business data
- SQL-style querying against normalized row storage
- Vector search fallback for exploratory questions
- Chart generation with PNG output
- CSV export generation
- Generated file metadata persistence

## Tech Stack

- FastAPI
- Python 3.11+
- Pandas + OpenPyXL
- PostgreSQL + psycopg
- Sentence Transformers
- LangChain
- OpenAI-compatible chat LLM via `langchain-openai`
- Matplotlib

## Main API Endpoints

### `POST /context/load`

Ingest an uploaded Excel file into structured and semantic storage.

### `POST /chat`

Run an agent-style analysis round. The LLM receives a lightweight data catalog and chooses tools such as `query_data`, `vector_search`, `generate_chart`, and `generate_csv`.

## Local Development

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Run the service:

```bash
source .venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Default local URL:

```text
http://localhost:8000
```

## Environment Variables

Typical local `.env`:

```bash
DATABASE_URL=postgresql://admin:password123@localhost:5432/sme_db
LLM_PROVIDER=openai-compatible
LLM_MODEL=qwen3.6-plus
OPENAI_COMPATIBLE_API_KEY=your_llm_api_key
OPENAI_COMPATIBLE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
EMBEDDING_MODEL=all-MiniLM-L6-v2
MAX_EXCEL_FILE_MB=25
MAX_ROWS_PER_SHEET=10000
EXCEL_CHUNK_SIZE=40
EXCEL_CHUNK_OVERLAP=8
GENERATED_FILES_DIR=/absolute/path/to/sme-backend/generats
```

Qwen/DashScope is configured through the same OpenAI-compatible variables shown
above. To use another OpenAI-compatible provider, replace `LLM_MODEL`,
`OPENAI_COMPATIBLE_API_KEY`, and `OPENAI_COMPATIBLE_BASE_URL`.

## Useful Commands

```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
bash migration/purge_all_files.sh
```

## Managed Data

This service writes or maintains records related to:

- `SheetMeta`
- `SheetRow`
- `GeneratedFile`
- semantic chunks / embeddings produced during ingestion

## Service Relationships

- Receives ingestion and chat requests from `sme-backend`
- Stores analysis artifacts in PostgreSQL
- Writes generated charts/files into a directory served by `sme-backend`
- Is not directly called by the browser
