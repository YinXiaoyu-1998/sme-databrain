from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from typing import Any, Optional

import numpy as np
import pandas as pd
import psycopg


def format_chat_history(history: Optional[list], limit: int = 6) -> str:
    if not history or not isinstance(history, list):
        return ""

    items_sorted = sorted(history, key=lambda x: x.get("createdAt", ""))
    last_chat_id = items_sorted[-1].get("chatId")
    if last_chat_id:
        items_sorted = [item for item in items_sorted if item.get("chatId") == last_chat_id]

    items_sorted = items_sorted[-limit:]
    lines = []
    for item in items_sorted:
        role = item.get("role", "")
        content = item.get("content", "")
        if not content:
            continue
        if role == "user":
            lines.append(f"用户: {content}")
        else:
            lines.append(f"助手: {content}")
    return "\n".join(lines).strip()


def custom_error_handler(error: Exception) -> str:
    error_str = str(error)
    match = re.search(r"Could not parse LLM output: `(.*)`", error_str, re.DOTALL)
    if match:
        return match.group(1).strip("`")
    if "Invalid Format: Missing 'Action:'" in error_str:
        return "分析已完成，但格式稍有偏差。请尝试在 Prompt 中强调只输出结论。"
    return str(error)


def is_excel_mime(mime_type: str) -> bool:
    normalized = (mime_type or "").lower()
    return "spreadsheet" in normalized or "excel" in normalized or normalized in {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    }


def file_sha256(filepath: str) -> str:
    digest = hashlib.sha256()
    with open(filepath, "rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(8192), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_db_connection() -> psycopg.Connection:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")
    return psycopg.connect(database_url)


def vector_to_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.10f}" for value in vector) + "]"


def _truncate_cell_value(value: Any, max_len: int = 180) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _convert_cell(val: Any) -> Any:
    """Convert a pandas/numpy cell value to a JSON-serializable Python type."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    try:
        if pd.isna(val):
            return None
    except (TypeError, ValueError):
        pass
    if hasattr(val, "item"):
        return val.item()
    return val


def _detect_header_row(filepath: str, sheet_name: str, max_scan: int = 10) -> int:
    """Auto-detect the header row by finding the first row with the most
    non-empty unique values among the first ``max_scan`` rows.

    Returns the 0-based row index to use as the header.
    """
    raw = pd.read_excel(filepath, sheet_name=sheet_name, header=None, nrows=max_scan)
    if raw.empty:
        return 0

    best_row = 0
    best_count = 0
    total_cols = len(raw.columns)

    for idx in range(len(raw)):
        row_vals = raw.iloc[idx]
        non_empty = set()
        for val in row_vals:
            if val is None:
                continue
            try:
                if pd.isna(val):
                    continue
            except (TypeError, ValueError):
                pass
            s = str(val).strip()
            if s:
                non_empty.add(s)

        if len(non_empty) > best_count:
            best_count = len(non_empty)
            best_row = idx
            if best_count == total_cols:
                break

    return best_row


def read_excel_workbook(
    filepath: str,
    max_rows_per_sheet: int = 10000,
) -> dict[str, pd.DataFrame]:
    """Read an Excel file and return a dict of sheet_name -> DataFrame.

    Auto-detects the header row per sheet by picking the first row with
    the most non-empty unique values. Validates row count limits.
    """
    sheet_names = pd.ExcelFile(filepath).sheet_names
    workbook: dict[str, pd.DataFrame] = {}

    for name in sheet_names:
        header_row = _detect_header_row(filepath, name)
        df = pd.read_excel(filepath, sheet_name=name, header=header_row)
        row_count = len(df.index)
        if row_count > max_rows_per_sheet:
            raise ValueError(
                f"Sheet '{name}' has {row_count} rows, exceeding limit {max_rows_per_sheet}"
            )
        workbook[name] = df

    return workbook


def chunk_excel_file(
    filepath: str = "",
    chunk_size: int = 40,
    chunk_overlap: int = 8,
    max_rows_per_sheet: int = 10000,
    *,
    workbook: dict[str, pd.DataFrame] | None = None,
) -> list[dict[str, Any]]:
    if workbook is None:
        workbook = read_excel_workbook(filepath, max_rows_per_sheet)

    chunks: list[dict[str, Any]] = []
    stride = max(chunk_size - chunk_overlap, 1)

    for sheet_name, df in workbook.items():
        sheet_df = df.fillna("").copy()
        row_count = len(sheet_df.index)
        if row_count == 0:
            continue

        columns = [str(col) for col in sheet_df.columns.tolist()]
        for start in range(0, row_count, stride):
            end = min(start + chunk_size, row_count)
            window = sheet_df.iloc[start:end]
            if window.empty:
                continue

            row_lines = []
            for idx, row in window.iterrows():
                pairs = []
                for col in columns:
                    cell = _truncate_cell_value(row.get(col, ""))
                    pairs.append(f"{col}: {cell}")
                row_lines.append(f"Row {idx + 1} | " + " | ".join(pairs))

            content = "\n".join(
                [
                    f"Sheet: {sheet_name}",
                    "Columns: " + " | ".join(columns),
                    f"RowRange: {start + 1}-{end}",
                    "Rows:",
                    *row_lines,
                ]
            )

            chunks.append(
                {
                    "content": content,
                    "metadata": {
                        "sheetName": sheet_name,
                        "rowStart": start + 1,
                        "rowEnd": end,
                        "columns": columns,
                    },
                }
            )

    return chunks


def ingest_structured_data(
    cursor: Any,
    *,
    workbook: dict[str, pd.DataFrame],
    file_id: str,
    document_id: str,
    user_id: str,
) -> int:
    """Insert SheetMeta and SheetRow records from an Excel workbook.

    Deletes any existing SheetMeta/SheetRow for this document first.
    Returns total rows inserted.
    """
    cursor.execute('DELETE FROM "SheetMeta" WHERE "documentId" = %s', (document_id,))
    cursor.execute('DELETE FROM "SheetRow" WHERE "documentId" = %s', (document_id,))

    total_rows = 0

    for sheet_name, df in workbook.items():
        row_count = len(df.index)
        if row_count == 0:
            continue

        columns = [str(col) for col in df.columns.tolist()]

        column_types: dict[str, str] = {}
        for col_name in columns:
            orig_col = [c for c in df.columns if str(c) == col_name]
            if orig_col and pd.api.types.is_numeric_dtype(df[orig_col[0]]):
                column_types[col_name] = "number"
            else:
                column_types[col_name] = "text"

        sample_values: dict[str, list] = {}
        for col_name in columns:
            orig_col = [c for c in df.columns if str(c) == col_name]
            if not orig_col:
                continue
            col_series = df[orig_col[0]].dropna()
            samples = []
            for val in col_series.head(3):
                converted = _convert_cell(val)
                if converted is not None:
                    if isinstance(converted, float):
                        samples.append(round(converted, 4))
                    else:
                        samples.append(converted)
            sample_values[col_name] = samples

        cursor.execute(
            """
            INSERT INTO "SheetMeta"
              ("id", "fileId", "documentId", "userId", "sheetName",
               "columns", "columnTypes", "rowCount", "sampleValues", "createdAt")
            VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s::jsonb, NOW())
            """,
            (
                str(uuid.uuid4()), file_id, document_id, user_id, str(sheet_name),
                json.dumps(columns, ensure_ascii=False),
                json.dumps(column_types, ensure_ascii=False),
                row_count,
                json.dumps(sample_values, ensure_ascii=False, default=str),
            ),
        )

        for idx in range(row_count):
            row = df.iloc[idx]
            row_data = {}
            for col_name in columns:
                orig_col = [c for c in df.columns if str(c) == col_name]
                val = row[orig_col[0]] if orig_col else None
                row_data[col_name] = _convert_cell(val)

            cursor.execute(
                """
                INSERT INTO "SheetRow"
                  ("id", "fileId", "documentId", "userId", "sheetName",
                   "rowIndex", "data", "createdAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
                """,
                (
                    str(uuid.uuid4()), file_id, document_id, user_id,
                    str(sheet_name), idx,
                    json.dumps(row_data, ensure_ascii=False, default=str),
                ),
            )
            total_rows += 1

    return total_rows
