
import logging
import os
import time
import uuid
import json
from typing import Any

from fastapi import FastAPI, HTTPException
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.messages import HumanMessage, ToolMessage
from dotenv import load_dotenv

from llm_factory import build_llm
from schemas import LoadContextRequest, ChatRequest
from utils import (
    chunk_excel_file,
    file_sha256,
    format_chat_history,
    get_db_connection,
    ingest_structured_data,
    is_excel_mime,
    read_excel_workbook,
    vector_to_literal,
)
from tools import build_tools

app = FastAPI(title="SME Data Brain", version="0.2.0")
logger = logging.getLogger("sme-databrain")
logging.basicConfig(level=logging.INFO)

load_dotenv()

llm = build_llm()

embedding_model_name = os.getenv("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
embedding_function = HuggingFaceEmbeddings(model_name=embedding_model_name)

max_excel_file_mb = int(os.getenv("MAX_EXCEL_FILE_MB", "25"))
max_rows_per_sheet = int(os.getenv("MAX_ROWS_PER_SHEET", "10000"))
chunk_size = int(os.getenv("EXCEL_CHUNK_SIZE", "40"))
chunk_overlap = int(os.getenv("EXCEL_CHUNK_OVERLAP", "8"))


def extract_llm_text(response: object) -> str:
    content = getattr(response, "content", response)
    if isinstance(content, str):
        return content.strip()

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    parts.append(text)
                continue
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    text = text.strip()
                    if text:
                        parts.append(text)
        if parts:
            return "\n".join(parts).strip()

    return str(content).strip()


def fetch_generated_files(file_ids: list[str]) -> list[dict]:
    if not file_ids:
        return []
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                SELECT "id", "fileType", "mimeType", "filename", "path", "size"
                FROM "GeneratedFile"
                WHERE "id" = ANY(%s)
                """,
                (file_ids,),
            )
            rows = cursor.fetchall()
    return [
        {
            "id": row[0],
            "fileType": row[1],
            "mimeType": row[2],
            "filename": row[3],
            "path": row[4],
            "size": row[5],
        }
        for row in rows
    ]


def build_data_catalog(*, user_id: str, file_id: str | None) -> tuple[str, list[str]]:
    """Build a compact data catalog string for the LLM prompt.

    Returns (catalog_text, user_file_ids).
    """
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            if file_id:
                cursor.execute(
                    """
                    SELECT sm."fileId", f."originalName", sm."sheetName",
                           sm."columns", sm."columnTypes", sm."rowCount",
                           sm."sampleValues"
                    FROM "SheetMeta" sm
                    JOIN "DataFile" f ON f."id" = sm."fileId"
                    WHERE sm."userId" = %s AND sm."fileId" = %s
                    ORDER BY f."originalName", sm."sheetName"
                    """,
                    (user_id, file_id),
                )
            else:
                cursor.execute(
                    """
                    SELECT sm."fileId", f."originalName", sm."sheetName",
                           sm."columns", sm."columnTypes", sm."rowCount",
                           sm."sampleValues"
                    FROM "SheetMeta" sm
                    JOIN "DataFile" f ON f."id" = sm."fileId"
                    WHERE sm."userId" = %s
                    ORDER BY f."originalName", sm."sheetName"
                    """,
                    (user_id,),
                )
            rows = cursor.fetchall()

    if not rows:
        return "", []

    files: dict[str, dict[str, Any]] = {}
    user_file_ids: list[str] = []
    for row in rows:
        fid, fname, sheet_name, columns, col_types, row_count, sample_values = row
        if fid not in files:
            files[fid] = {"name": fname, "sheets": []}
            user_file_ids.append(fid)

        col_types = col_types or {}
        sample_values = sample_values or {}
        col_descs = []
        for col in columns:
            ctype = col_types.get(col, "text")
            samples = sample_values.get(col, [])
            sample_str = ""
            if samples:
                previews = [str(s) for s in samples[:2]]
                sample_str = f', 例: {", ".join(previews)}'
            col_descs.append(f"{col}({ctype}{sample_str})")

        files[fid]["sheets"].append(
            f'  Sheet "{sheet_name}" ({row_count}行): {", ".join(col_descs)}'
        )

    lines: list[str] = []
    for fid, info in files.items():
        lines.append(f'文件: "{info["name"]}" (fileId: {fid})')
        lines.extend(info["sheets"])

    return "\n".join(lines), user_file_ids


@app.get("/")
def read_root():
    return {"status": "SME DataBrain is running"}


# =========================================================================
# Ingestion endpoint
# =========================================================================

@app.post("/context/load")
async def load_context(request: LoadContextRequest):
    print(request)
    print("--------------------------------")
    file_path = request.filepath.strip()
    mime_type = request.mimeType.strip()
    file_id = request.fileId.strip()

    if not file_id:
        raise HTTPException(status_code=400, detail="fileId is required")
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"File not found at {file_path}")
    if not is_excel_mime(mime_type):
        raise HTTPException(status_code=400, detail="Only Excel ingestion is enabled in this release")
    if os.path.getsize(file_path) > max_excel_file_mb * 1024 * 1024:
        raise HTTPException(
            status_code=400,
            detail=f"Excel file exceeds max size of {max_excel_file_mb}MB",
        )

    start = time.perf_counter()
    document_id: str | None = None
    ingestion_run_id: str | None = None

    try:
        content_hash = file_sha256(file_path)
        workbook = read_excel_workbook(file_path, max_rows_per_sheet)
        chunks = chunk_excel_file(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            max_rows_per_sheet=max_rows_per_sheet,
            workbook=workbook,
        )
        if not chunks:
            raise HTTPException(status_code=400, detail="Excel file does not contain readable rows")

        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                # Look up userId from DataFile
                cursor.execute(
                    'SELECT "userId" FROM "DataFile" WHERE "id" = %s',
                    (file_id,),
                )
                file_owner = cursor.fetchone()
                owner_user_id = file_owner[0] if file_owner else ""

                cursor.execute(
                    """
                    SELECT "id", "contentHash", "status", "chunkCount"
                    FROM "Document"
                    WHERE "fileId" = %s
                    """,
                    (file_id,),
                )
                existing = cursor.fetchone()

                # Check if document is unchanged AND structured data exists
                has_sheet_meta = False
                if existing:
                    cursor.execute(
                        'SELECT COUNT(*) FROM "SheetMeta" WHERE "documentId" = %s',
                        (existing[0],),
                    )
                    has_sheet_meta = (cursor.fetchone()[0] or 0) > 0

                if (
                    existing
                    and existing[1] == content_hash
                    and existing[2] == "COMPLETED"
                    and int(existing[3] or 0) > 0
                    and has_sheet_meta
                ):
                    duration_ms = int((time.perf_counter() - start) * 1000)
                    ingestion_run_id = str(uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT INTO "RAGIngestionRun"
                          ("id", "documentId", "fileId", "status", "chunksIndexed", "durationMs", "createdAt", "updatedAt", "completedAt")
                        VALUES (%s, %s, %s, 'COMPLETED', 0, %s, NOW(), NOW(), NOW())
                        """,
                        (ingestion_run_id, existing[0], file_id, duration_ms),
                    )
                    conn.commit()
                    return {
                        "message": "Document unchanged; existing data reused",
                        "fileId": file_id,
                        "documentId": existing[0],
                        "status": "SKIPPED",
                        "chunksIndexed": int(existing[3] or 0),
                    }

                if existing:
                    document_id = existing[0]
                    cursor.execute(
                        """
                        UPDATE "Document"
                        SET "contentHash" = %s, "mimeType" = %s, "status" = 'PROCESSING',
                            "chunkCount" = 0, "errorMessage" = NULL, "updatedAt" = NOW()
                        WHERE "id" = %s
                        """,
                        (content_hash, mime_type, document_id),
                    )
                    cursor.execute('DELETE FROM "Chunk" WHERE "documentId" = %s', (document_id,))
                else:
                    document_id = str(uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT INTO "Document"
                          ("id", "fileId", "contentHash", "status", "mimeType", "chunkCount", "createdAt", "updatedAt")
                        VALUES (%s, %s, %s, 'PROCESSING', %s, 0, NOW(), NOW())
                        """,
                        (document_id, file_id, content_hash, mime_type),
                    )

                ingestion_run_id = str(uuid.uuid4())
                cursor.execute(
                    """
                    INSERT INTO "RAGIngestionRun"
                      ("id", "documentId", "fileId", "status", "chunksIndexed", "createdAt", "updatedAt")
                    VALUES (%s, %s, %s, 'PROCESSING', 0, NOW(), NOW())
                    """,
                    (ingestion_run_id, document_id, file_id),
                )

                # --- Chunk + Embedding pipeline (existing) ---
                vectors = embedding_function.embed_documents([item["content"] for item in chunks])
                for idx, (chunk, vector) in enumerate(zip(chunks, vectors)):
                    chunk_id = str(uuid.uuid4())
                    cursor.execute(
                        """
                        INSERT INTO "Chunk" ("id", "documentId", "chunkIndex", "content", "metadata", "createdAt")
                        VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
                        """,
                        (
                            chunk_id,
                            document_id,
                            idx,
                            chunk["content"],
                            json.dumps(chunk["metadata"], ensure_ascii=False),
                        ),
                    )
                    cursor.execute(
                        """
                        INSERT INTO "Embedding" ("id", "chunkId", "vector", "model", "createdAt")
                        VALUES (%s, %s, %s::vector, %s, NOW())
                        """,
                        (str(uuid.uuid4()), chunk_id, vector_to_literal(vector), embedding_model_name),
                    )

                # --- Structured data pipeline (NEW) ---
                structured_rows = ingest_structured_data(
                    cursor,
                    workbook=workbook,
                    file_id=file_id,
                    document_id=document_id,
                    user_id=owner_user_id,
                )
                logger.info(
                    "structured_data_ingested file_id=%s rows=%s",
                    file_id, structured_rows,
                )

                duration_ms = int((time.perf_counter() - start) * 1000)
                cursor.execute(
                    """
                    UPDATE "Document"
                    SET "status" = 'COMPLETED', "chunkCount" = %s, "processedAt" = NOW(), "updatedAt" = NOW()
                    WHERE "id" = %s
                    """,
                    (len(chunks), document_id),
                )
                cursor.execute(
                    """
                    UPDATE "RAGIngestionRun"
                    SET "status" = 'COMPLETED', "chunksIndexed" = %s, "durationMs" = %s, "updatedAt" = NOW(), "completedAt" = NOW()
                    WHERE "id" = %s
                    """,
                    (len(chunks), duration_ms, ingestion_run_id),
                )
                conn.commit()

                logger.info(
                    "ingestion_completed file_id=%s document_id=%s chunks=%s structured_rows=%s duration_ms=%s",
                    file_id, document_id, len(chunks), structured_rows, duration_ms,
                )
                return {
                    "message": "Excel ingested and indexed successfully",
                    "fileId": file_id,
                    "documentId": document_id,
                    "status": "COMPLETED",
                    "chunksIndexed": len(chunks),
                    "structuredRows": structured_rows,
                }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("excel_ingestion_failed file_id=%s error=%s", file_id, str(exc))
        duration_ms = int((time.perf_counter() - start) * 1000)
        try:
            with get_db_connection() as conn:
                with conn.cursor() as cursor:
                    if document_id:
                        cursor.execute(
                            """
                            UPDATE "Document"
                            SET "status" = 'FAILED', "errorMessage" = %s, "updatedAt" = NOW()
                            WHERE "id" = %s
                            """,
                            (str(exc), document_id),
                        )
                    if ingestion_run_id:
                        cursor.execute(
                            """
                            UPDATE "RAGIngestionRun"
                            SET "status" = 'FAILED', "errorMessage" = %s, "durationMs" = %s, "updatedAt" = NOW(), "completedAt" = NOW()
                            WHERE "id" = %s
                            """,
                            (str(exc), duration_ms, ingestion_run_id),
                        )
                    conn.commit()
        except Exception:
            logger.exception("failed_to_update_ingestion_failure_status file_id=%s", file_id)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {str(exc)}")


# =========================================================================
# Chat endpoint
# =========================================================================

@app.post("/chat")
async def chat(request: ChatRequest):
    question = request.message.strip()
    user_id = request.userId.strip()
    file_id = request.fileId.strip() if request.fileId else None
    chat_id = request.chatId

    if not question:
        raise HTTPException(status_code=400, detail="message cannot be empty")
    if not user_id:
        raise HTTPException(status_code=400, detail="userId is required")

    history_text = format_chat_history(request.history)

    try:
        # 1. Build lightweight data catalog
        catalog_text, user_file_ids = build_data_catalog(
            user_id=user_id, file_id=file_id,
        )
        print("Step 1: Built data catalog, length:", len(catalog_text))
        print("--------------------------------")

        if not catalog_text:
            return {
                "answer": "当前用户下暂无可查询数据，请先上传并处理 Excel 文件。",
                "generatedFiles": [],
            }

        # 2. Build prompt (small: question + history + catalog only)
        prompt = (
            "你是一个经验丰富的营销分析师。\n"
            "请严格遵守以下要求：\n"
            "1) 严格使用中文回答。\n"
            "2) 先使用 query_data 工具查询所需数据，再基于查询结果进行分析。不要凭空猜测数据。\n"
            "3) 如果问题比较模糊或探索性的，可以使用 vector_search 工具进行语义搜索。\n"
            "4) 当用户需要可视化对比、趋势或分布等数据时，请调用 generate_chart 工具生成图表。\n"
            "5) 当用户需要可导出的数据表格时，请调用 generate_csv 工具生成 CSV 文件。\n"
            "6) 回答结构尽量清晰：先给结论，再给关键依据（可用要点列出）。\n"
            "7) 生成图表或文件时，标题和文件名必须使用中文，不要使用英文或拼音。\n"
            "8) 生成图表或文件时，仍需在文字回答中包含分析结论和关键发现。\n\n"
            f"该用户可查询的数据（调用 query_data 工具时，sheet_name 和 columns 参数可以从以下目录中选取）:\n{catalog_text}\n\n"
            f"历史对话:\n{history_text or '无'}\n\n"
            f"用户问题: {question}\n"
        )
        print("Step 2: Built prompt, length:", len(prompt))
        print("--------------------------------")

        # 3. Build tools and invoke LLM
        tools = build_tools(
            user_id=user_id,
            chat_id=chat_id,
            embedding_function=embedding_function,
            user_file_ids=user_file_ids,
        )
        llm_with_tools = llm.bind_tools(tools)
        tool_map = {t.name: t for t in tools}

        messages_chain = [HumanMessage(content=prompt)]
        ai_msg = llm_with_tools.invoke(messages_chain)
        generated_file_ids: list[str] = []

        max_tool_rounds = 8
        rounds = 0
        while ai_msg.tool_calls and rounds < max_tool_rounds:
            rounds += 1
            tool_names = [tc["name"] for tc in ai_msg.tool_calls]
            print(f"Step 3.{rounds}: Executing tool call(s): {tool_names}")
            print("--------------------------------")
            messages_chain.append(ai_msg)
            for tc in ai_msg.tool_calls:
                try:
                    result = tool_map[tc["name"]].invoke(tc["args"])
                except Exception as tool_err:
                    logger.exception("tool_call_failed tool=%s", tc["name"])
                    result = json.dumps(
                        {"error": f"工具调用失败: {str(tool_err)}"},
                        ensure_ascii=False,
                    )
                messages_chain.append(
                    ToolMessage(content=str(result), tool_call_id=tc["id"])
                )
                try:
                    result_data = json.loads(result) if isinstance(result, str) else result
                    if isinstance(result_data, dict) and "fileId" in result_data:
                        generated_file_ids.append(result_data["fileId"])
                except (json.JSONDecodeError, TypeError):
                    pass
            ai_msg = llm_with_tools.invoke(messages_chain)

        answer = extract_llm_text(ai_msg)
        answer_log = answer if len(answer) <= 1000 else answer[:1000] + "...[truncated]"
        print("Step 4: Final answer:", answer_log)
        print("--------------------------------")

        generated_files = fetch_generated_files(generated_file_ids) if generated_file_ids else []

        return {
            "answer": answer,
            "generatedFiles": generated_files,
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("chat_failed user_id=%s file_id=%s error=%s", user_id, file_id, str(exc))
        raise HTTPException(status_code=500, detail=f"Chat failed: {str(exc)}")
