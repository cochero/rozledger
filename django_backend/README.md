# RozLedger Django + MySQL Backend

This is the recommended backend for RozLedger. It stores leads, invoice submissions and affiliate clicks in MySQL and provides a Django admin panel.

## 1. Create the MySQL database

Run this in MySQL, or execute `mysql_init.sql` after changing the password:

```sql
CREATE DATABASE rozledger CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'rozledger_user'@'localhost' IDENTIFIED BY 'change-this-password';
GRANT ALL PRIVILEGES ON rozledger.* TO 'rozledger_user'@'localhost';
FLUSH PRIVILEGES;
```

## 2. Configure environment

Copy the example file:

```powershell
cd C:\Projects\RozLedger\django_backend
Copy-Item .env.example .env
```

Edit `.env` and set your real MySQL username and password.

## 3. Install dependencies

```powershell
cd C:\Projects\RozLedger\django_backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## 4. Create tables and admin user

```powershell
python manage.py migrate
python manage.py createsuperuser
```

## 5. Run

```powershell
python manage.py runserver 127.0.0.1:8000
```

Open:

`http://127.0.0.1:8000`

Admin:

`http://127.0.0.1:8000/admin/`

Health check:

`http://127.0.0.1:8000/api/health`

## API endpoints

- `POST /api/leads`
- `POST /api/invoices`
- `POST /api/affiliate-clicks`
- `GET /api/options`
- `GET /api/health`
