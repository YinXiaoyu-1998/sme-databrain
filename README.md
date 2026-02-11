# SME Data Brain (FastAPI)

## Commands

Install dependencies (first time):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Run the project locally:

```bash
source venv/bin/activate
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Run tests:

```bash
python -m pytest
```
