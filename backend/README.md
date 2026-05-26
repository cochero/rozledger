# RozLedger Backend

This backend serves the frontend and saves leads, invoices and affiliate-click events in SQLite.

## Run locally

```powershell
cd C:\Projects\RozLedger\backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app.py
```

Open:

`http://127.0.0.1:8000`

Health check:

`http://127.0.0.1:8000/api/health`

The database will be created at:

`C:\Projects\RozLedger\data\rozledger.db`
