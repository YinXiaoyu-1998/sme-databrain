#!/bin/bash
# Full purge: delete all uploaded + generated files from disk and database.
# Usage: bash migration/purge_all_files.sh
# WARNING: This is irreversible.

set -e

echo "=== Purging disk files ==="
rm -rf /Users/xiaoyuyin/Desktop/YXY_DEV/SME/sme-backend/uploads/*
rm -rf /Users/xiaoyuyin/Desktop/YXY_DEV/SME/sme-backend/generats/*
echo "Disk files deleted."

echo "=== Purging database records ==="
cd "$(dirname "$0")/.."
source .venv/bin/activate
python3 -c "
import psycopg
conn = psycopg.connect('postgresql://admin:password123@localhost:5432/sme_db')
with conn.cursor() as cur:
    cur.execute(open('migration/purge_all_files.sql', encoding='utf-8').read())
conn.commit()
conn.close()
print('Database records purged.')
"

echo "=== Purge complete ==="
