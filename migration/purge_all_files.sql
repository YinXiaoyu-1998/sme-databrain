-- ============================================================
-- Full disk file purge: clears all uploaded + generated file
-- records from the database.
--
-- Run AFTER deleting the actual files from disk:
--   rm -rf /Users/xiaoyuyin/Desktop/YXY_DEV/SME/sme-backend/uploads/*
--   rm -rf /Users/xiaoyuyin/Desktop/YXY_DEV/SME/sme-backend/generats/*
--
-- Then execute this SQL:
--   source .venv/bin/activate
--   python3 -c "
--   import psycopg
--   conn = psycopg.connect('postgresql://admin:password123@localhost:5432/sme_db')
--   with conn.cursor() as cur:
--       cur.execute(open('migration/purge_all_files.sql', encoding='utf-8').read())
--   conn.commit()
--   conn.close()
--   print('Purge complete')
--   "
--
-- WARNING: This is irreversible. All file data will be lost.
-- User accounts, sessions, and message text are NOT affected.
-- ============================================================

-- 1. Vector embeddings (FK cascades from Chunk, but explicit for safety)
DELETE FROM "Embedding";

-- 2. Text chunks (FK cascades from Document)
DELETE FROM "Chunk";

-- 3. Ingestion run history
DELETE FROM "RAGIngestionRun";

-- 4. Document records (1:1 with DataFile)
DELETE FROM "Document";

-- 5. Structured row data (no FK cascade)
DELETE FROM "SheetRow";

-- 6. Sheet metadata catalog (no FK cascade)
DELETE FROM "SheetMeta";

-- 7. LLM-generated files (charts, CSVs)
DELETE FROM "GeneratedFile";

-- 8. Uploaded file registry (root of the chain)
DELETE FROM "DataFile";
