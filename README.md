# Smart Library Management System

A responsive campus library application built with Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF, and SQLite. It supports public book search, student accounts, librarian administration, circulation, and estimated late fines.

## Requirements

- Windows 10 or 11
- Python 3.10 or newer
- VS Code with the Python extension (recommended)

## Install and run in VS Code

1. Open the project folder in VS Code: **File → Open Folder…** and select the folder containing `app.py`.
2. Open **Terminal → New Terminal**. In PowerShell, create a virtual environment and activate it:

   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   ```

   If PowerShell blocks activation, use the current-process-only setting, then activate:

   ```powershell
   Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
   .\.venv\Scripts\Activate.ps1
   ```

3. Install dependencies:

   ```powershell
   python -m pip install --upgrade pip
   pip install -r requirements.txt
   ```

4. Set a secret key for this terminal session:

   ```powershell
   $env:SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"
   ```

5. Create the SQLite tables:

   ```powershell
   flask --app app init-db
   ```

6. Create a librarian account. The CLI prompts for the email, name, and password without storing credentials in source:

   ```powershell
   flask --app app create-admin
   ```

   The administrator password must be at least 12 characters. Public registration only creates student accounts.

7. Start the development server:

   ```powershell
   flask --app app run --debug
   ```

8. Open the URL printed in the terminal (normally `http://127.0.0.1:5000`). The home page and catalogue are public. Use **Sign in** for the librarian or student account.

In VS Code, select the `.venv` interpreter using **Python: Select Interpreter** if the editor does not detect it automatically. To stop the server, focus its terminal and press `Ctrl+C`.

## Run tests

Activate the virtual environment, then run:

```powershell
python -m pytest
```

Tests use an in-memory SQLite database and disable CSRF only for the test client. CSRF protection remains enabled in the application.

## Main workflows

- Students register with their name, unique student ID, email, and password; passwords are stored as Werkzeug hashes.
- Students browse and search the public catalogue, sign in, and view only their own loan history and due dates.
- Librarians add, edit, search, and delete catalogue entries. A book with an active loan cannot be deleted, and total stock cannot be reduced below the number of active loans.
- Librarians issue an available copy to an active student. The default due date is 14 days after issue; issuing atomically reduces available stock.
- Librarians record returns. A return is accepted once and restores one copy. The estimated late fine is Rs. 2 per day after the due date; the estimate is informational and is not a payment transaction.
- Librarians can pause or reactivate student accounts and review all active, overdue, and returned loans.

## Data and configuration

The default SQLite database is `instance/smart_library.db`; the `instance` folder is excluded from Git. To use another database, set `DATABASE_URL` before running Flask. Set a stable, high-entropy `SECRET_KEY` in the environment for deployments. For a production HTTPS deployment, set `SESSION_COOKIE_SECURE=true` and use a production WSGI server; Flask's debug server is for local development only.

The app creates its tables only when `flask --app app init-db` is run; it does not reset or erase existing data at startup. Back up the SQLite database file before making manual changes.
