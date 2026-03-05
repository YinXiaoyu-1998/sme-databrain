CREATE TABLE IF NOT EXISTS "SheetMeta" (
    "id"           TEXT        PRIMARY KEY,
    "fileId"       TEXT        NOT NULL,
    "documentId"   TEXT        NOT NULL,
    "userId"       TEXT        NOT NULL,
    "sheetName"    TEXT        NOT NULL,
    "columns"      JSONB       NOT NULL,
    "columnTypes"  JSONB,
    "rowCount"     INTEGER     NOT NULL DEFAULT 0,
    "sampleValues" JSONB,
    "createdAt"    TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS "SheetMeta_fileId_idx" ON "SheetMeta"("fileId");
CREATE INDEX IF NOT EXISTS "SheetMeta_documentId_idx" ON "SheetMeta"("documentId");
CREATE INDEX IF NOT EXISTS "SheetMeta_userId_idx" ON "SheetMeta"("userId");

CREATE TABLE IF NOT EXISTS "SheetRow" (
    "id"           TEXT        PRIMARY KEY,
    "fileId"       TEXT        NOT NULL,
    "documentId"   TEXT        NOT NULL,
    "userId"       TEXT        NOT NULL,
    "sheetName"    TEXT        NOT NULL,
    "rowIndex"     INTEGER     NOT NULL,
    "data"         JSONB       NOT NULL,
    "createdAt"    TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS "SheetRow_fileId_sheetName_idx" ON "SheetRow"("fileId", "sheetName");
CREATE INDEX IF NOT EXISTS "SheetRow_documentId_idx" ON "SheetRow"("documentId");
CREATE INDEX IF NOT EXISTS "SheetRow_userId_sheetName_idx" ON "SheetRow"("userId", "sheetName");
CREATE INDEX IF NOT EXISTS "SheetRow_data_gin_idx" ON "SheetRow" USING GIN ("data");
