from __future__ import annotations

import json
import logging
import os
import uuid

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from langchain_core.tools import tool

from utils import get_db_connection, vector_to_literal

logger = logging.getLogger("sme-databrain.tools")


def _get_output_dir() -> str:
    return os.getenv(
        "GENERATED_FILES_DIR",
        "/Users/xiaoyuyin/Desktop/YXY_DEV/SME/sme-backend/generats",
    )


def _ensure_output_dir() -> str:
    output_dir = _get_output_dir()
    os.makedirs(output_dir, exist_ok=True)
    return output_dir


def _insert_generated_file(
    *,
    file_id: str,
    user_id: str,
    chat_id: str | None,
    file_type: str,
    mime_type: str,
    filename: str,
    path: str,
    size: int,
    metadata: dict | None,
) -> None:
    with get_db_connection() as conn:
        with conn.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO "GeneratedFile"
                  ("id", "userId", "chatId", "fileType", "mimeType",
                   "filename", "path", "size", "metadata", "createdAt", "updatedAt")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW(), NOW())
                """,
                (
                    file_id, user_id, chat_id, file_type, mime_type,
                    filename, path, size,
                    json.dumps(metadata, ensure_ascii=False) if metadata else None,
                ),
            )
        conn.commit()


def build_tools(
    *,
    user_id: str,
    chat_id: str | None,
    embedding_function=None,
    user_file_ids: list[str] | None = None,
) -> list:
    """Build LangChain tools with user/chat context bound via closure."""

    # ------------------------------------------------------------------
    # Tool 1: query_data
    # ------------------------------------------------------------------
    @tool
    def query_data(
        sheet_name: str,
        columns: list[str],
        sort_by: str = "",
        sort_order: str = "desc",
        limit: int = 50,
        file_id: str = "",
        filter_column: str = "",
        filter_operator: str = "",
        filter_value: str = "",
    ) -> str:
        """从结构化数据中查询指定Sheet的数据行。

        Args:
            sheet_name: Sheet名称（必须与数据目录中的名称一致）
            columns: 要返回的列名列表
            sort_by: 排序列名（可选）
            sort_order: 排序方向 "asc" 或 "desc"（默认 desc）
            limit: 返回最大行数（默认50，最大200）
            file_id: 限定到某个文件（可选，不指定则搜索该用户所有文件）
            filter_column: 过滤列名（可选）
            filter_operator: 过滤操作符，可选 ">", "<", ">=", "<=", "=", "!=", "contains"
            filter_value: 过滤值（可选）

        Returns:
            查询结果的JSON字符串，包含 columns、rows、count
        """
        logger.info(
            "query_data called: sheet_name=%s columns=%s sort_by=%s sort_order=%s "
            "limit=%s file_id=%s filter=%s %s %s",
            sheet_name, columns, sort_by, sort_order,
            limit, file_id, filter_column, filter_operator, filter_value,
        )
        limit = min(max(1, limit), 200)

        where_clauses = ['"userId" = %s']
        params: list = [user_id]

        where_clauses.append('"sheetName" = %s')
        params.append(sheet_name)

        if file_id:
            where_clauses.append('"fileId" = %s')
            params.append(file_id)
        elif user_file_ids:
            where_clauses.append('"fileId" = ANY(%s)')
            params.append(user_file_ids)

        valid_ops = {
            ">": ">", "<": "<", ">=": ">=", "<=": "<=",
            "=": "=", "!=": "!=", "contains": "LIKE",
        }
        if filter_column and filter_operator and filter_value is not None:
            op = valid_ops.get(filter_operator)
            if op:
                if filter_operator == "contains":
                    where_clauses.append('"data"->>%s LIKE %s')
                    params.extend([filter_column, f"%{filter_value}%"])
                elif filter_operator in (">", "<", ">=", "<="):
                    where_clauses.append(f'("data"->>%s)::numeric {op} %s::numeric')
                    params.extend([filter_column, filter_value])
                else:
                    where_clauses.append(f'"data"->>%s {op} %s')
                    params.extend([filter_column, filter_value])

        where_sql = " AND ".join(where_clauses)

        order_sql = ""
        if sort_by:
            sort_dir = "DESC" if sort_order.upper() != "ASC" else "ASC"
            order_sql = (
                f"ORDER BY "
                f"CASE WHEN \"data\"->>%s ~ '^-?[0-9]+(\\.[0-9]+)?$' "
                f"THEN (\"data\"->>%s)::numeric ELSE NULL END {sort_dir} NULLS LAST, "
                f"\"data\"->>%s {sort_dir}"
            )
            params.extend([sort_by, sort_by, sort_by])

        params.append(limit)

        query = f"""
            SELECT "rowIndex", "data"
            FROM "SheetRow"
            WHERE {where_sql}
            {order_sql}
            LIMIT %s
        """

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

        result_rows = []
        for row in rows:
            row_data = row[1] or {}
            filtered = {col: row_data.get(col) for col in columns}
            result_rows.append(filtered)

        return json.dumps(
            {"columns": columns, "rows": result_rows, "count": len(result_rows)},
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # Tool 2: vector_search
    # ------------------------------------------------------------------
    @tool
    def vector_search(
        query: str,
        file_id: str = "",
        top_k: int = 5,
    ) -> str:
        """语义搜索，从已索引文档中查找与查询语义相关的内容片段。适用于模糊、探索性的问题。

        Args:
            query: 搜索查询文本
            file_id: 限定到某个文件的fileId（可选）
            top_k: 返回最相关的结果数量（默认5，最大10）

        Returns:
            匹配的文档片段内容
        """
        logger.info(
            "vector_search called: query=%s file_id=%s top_k=%s",
            query[:200], file_id, top_k,
        )
        if embedding_function is None:
            return json.dumps(
                {"error": "向量搜索未启用"}, ensure_ascii=False,
            )

        top_k = min(max(1, top_k), 10)
        query_vector = embedding_function.embed_query(query)
        query_vec_lit = vector_to_literal(query_vector)
        target_file = file_id or None

        with get_db_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT c."content", c."metadata",
                           1 - (e."vector" <=> %s::vector) AS score,
                           d."fileId", f."originalName"
                    FROM "Document" d
                    JOIN "DataFile" f ON f."id" = d."fileId"
                    JOIN "Chunk" c ON c."documentId" = d."id"
                    JOIN "Embedding" e ON e."chunkId" = c."id"
                    WHERE d."status" = 'COMPLETED'
                      AND f."userId" = %s
                      AND (%s::text IS NULL OR d."fileId" = %s)
                    ORDER BY e."vector" <=> %s::vector
                    LIMIT %s
                    """,
                    (
                        query_vec_lit, user_id,
                        target_file, target_file,
                        query_vec_lit, top_k,
                    ),
                )
                rows = cur.fetchall()

        if not rows:
            return json.dumps(
                {"results": [], "message": "未找到相关内容"},
                ensure_ascii=False,
            )

        results = []
        for row in rows:
            content = row[0] or ""
            score = float(row[2] or 0.0)
            file_name = row[4] or ""
            preview = content[:800] if len(content) > 800 else content
            results.append(f"[相关度: {score:.2f} | 文件: {file_name}]\n{preview}")

        return "\n\n---\n\n".join(results)

    # ------------------------------------------------------------------
    # Tool 3: generate_chart
    # ------------------------------------------------------------------
    @tool
    def generate_chart(
        chart_type: str,
        title: str,
        x_labels: list[str],
        datasets: list[dict],
        x_axis_label: str = "",
        y_axis_label: str = "",
    ) -> str:
        """生成图表图片。

        Args:
            chart_type: 图表类型，可选 "bar"（柱状图）、"line"（折线图）、"pie"（饼图）
            title: 图表标题
            x_labels: X轴分类标签列表
            datasets: 数据系列列表，每个元素包含 "label"（系列名称）和 "values"（数值列表）
            x_axis_label: X轴标签（可选）
            y_axis_label: Y轴标签（可选）

        Returns:
            包含生成文件ID和文件名的JSON字符串
        """
        logger.info(
            "generate_chart called: chart_type=%s title=%s x_labels=%s "
            "datasets_count=%s x_axis=%s y_axis=%s",
            chart_type, title, x_labels,
            len(datasets), x_axis_label, y_axis_label,
        )
        output_dir = _ensure_output_dir()
        file_id_gen = str(uuid.uuid4())
        safe_title = (
            "".join(c for c in title if c.isalnum() or c in "-_ ").strip() or "chart"
        )
        filename = f"{file_id_gen}_{safe_title}.png"
        filepath = os.path.join(output_dir, filename)

        plt.rcParams["font.sans-serif"] = [
            "Arial Unicode MS", "SimHei", "WenQuanYi Micro Hei", "DejaVu Sans",
        ]
        plt.rcParams["axes.unicode_minus"] = False

        fig, ax = plt.subplots(figsize=(10, 6))

        if chart_type == "pie":
            values = datasets[0]["values"] if datasets else []
            ax.pie(values, labels=x_labels, autopct="%1.1f%%", startangle=90)
            ax.set_title(title, fontsize=14)
        elif chart_type == "line":
            for ds in datasets:
                ax.plot(x_labels, ds["values"], marker="o", label=ds.get("label", ""))
            ax.set_title(title, fontsize=14)
            if x_axis_label:
                ax.set_xlabel(x_axis_label)
            if y_axis_label:
                ax.set_ylabel(y_axis_label)
            if len(datasets) > 1:
                ax.legend()
            ax.grid(True, alpha=0.3)
        else:
            x = np.arange(len(x_labels))
            width = 0.8 / max(len(datasets), 1)
            for i, ds in enumerate(datasets):
                offset = (i - (len(datasets) - 1) / 2) * width
                ax.bar(x + offset, ds["values"], width, label=ds.get("label", ""))
            ax.set_xticks(x)
            ax.set_xticklabels(x_labels, rotation=45, ha="right")
            ax.set_title(title, fontsize=14)
            if x_axis_label:
                ax.set_xlabel(x_axis_label)
            if y_axis_label:
                ax.set_ylabel(y_axis_label)
            if len(datasets) > 1:
                ax.legend()

        plt.tight_layout()
        fig.savefig(filepath, dpi=150, bbox_inches="tight")
        plt.close(fig)

        file_size = os.path.getsize(filepath)
        _insert_generated_file(
            file_id=file_id_gen,
            user_id=user_id,
            chat_id=chat_id,
            file_type="chart",
            mime_type="image/png",
            filename=filename,
            path=filepath,
            size=file_size,
            metadata={
                "chartType": chart_type,
                "title": title,
                "xLabels": x_labels,
                "datasets": datasets,
            },
        )
        return json.dumps(
            {"fileId": file_id_gen, "filename": filename, "fileType": "chart"},
            ensure_ascii=False,
        )

    # ------------------------------------------------------------------
    # Tool 4: generate_csv
    # ------------------------------------------------------------------
    @tool
    def generate_csv(
        filename: str,
        headers: list[str],
        rows: list[list[str]],
    ) -> str:
        """生成CSV数据文件。

        Args:
            filename: 文件名（例如 "销售汇总.csv"）
            headers: 列标题列表
            rows: 数据行列表，每行是一个字符串值列表，与列标题对应

        Returns:
            包含生成文件ID和文件名的JSON字符串
        """
        logger.info(
            "generate_csv called: filename=%s headers=%s rows_count=%s",
            filename, headers, len(rows),
        )
        output_dir = _ensure_output_dir()
        file_id_gen = str(uuid.uuid4())
        if not filename.endswith(".csv"):
            filename += ".csv"
        safe_filename = f"{file_id_gen}_{filename}"
        filepath = os.path.join(output_dir, safe_filename)

        df = pd.DataFrame(rows, columns=headers)
        df.to_csv(filepath, index=False, encoding="utf-8-sig")

        file_size = os.path.getsize(filepath)
        _insert_generated_file(
            file_id=file_id_gen,
            user_id=user_id,
            chat_id=chat_id,
            file_type="csv",
            mime_type="text/csv",
            filename=safe_filename,
            path=filepath,
            size=file_size,
            metadata={"headers": headers, "rowCount": len(rows)},
        )
        return json.dumps(
            {"fileId": file_id_gen, "filename": safe_filename, "fileType": "csv"},
            ensure_ascii=False,
        )

    return [query_data, vector_search, generate_chart, generate_csv]
