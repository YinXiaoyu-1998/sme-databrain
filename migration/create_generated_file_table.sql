CREATE TABLE IF NOT EXISTS "GeneratedFile" (
    "id"        TEXT        PRIMARY KEY,
    "messageId" TEXT,
    "chatId"    TEXT,
    "userId"    TEXT        NOT NULL,
    "fileType"  TEXT        NOT NULL,
    "mimeType"  TEXT        NOT NULL,
    "filename"  TEXT        NOT NULL,
    "path"      TEXT        NOT NULL,
    "size"      INTEGER     NOT NULL DEFAULT 0,
    "metadata"  JSONB,
    "createdAt" TIMESTAMP   NOT NULL DEFAULT NOW(),
    "updatedAt" TIMESTAMP   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS "GeneratedFile_messageId_idx" ON "GeneratedFile"("messageId");
CREATE INDEX IF NOT EXISTS "GeneratedFile_chatId_idx" ON "GeneratedFile"("chatId");
CREATE INDEX IF NOT EXISTS "GeneratedFile_userId_idx" ON "GeneratedFile"("userId");
