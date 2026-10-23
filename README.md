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

## Configure password reset email

Registration verification codes, password reset links, and loan reminders are sent through Gmail SMTP. Verification codes expire after 10 minutes. Use a Gmail account with 2-Step Verification enabled, then create an **App Password** in Google Account security settings. Do not use your normal Gmail password or commit these values to source control. Configure these user environment variables before starting the app:

```powershell
$env:MAIL_USERNAME = "your-address@gmail.com"
$env:MAIL_PASSWORD = "your-16-character-app-password"
$env:MAIL_DEFAULT_SENDER = $env:MAIL_USERNAME
```

The defaults use `smtp.gmail.com`, port `587`, and STARTTLS. Optional settings are `MAIL_SERVER`, `MAIL_PORT`, `MAIL_USE_TLS`, and `PASSWORD_RESET_TIMEOUT` (seconds). A stable `SECRET_KEY` is required so reset links remain valid while the server is running.

### Schedule due-date reminders on Windows

The app sends one reminder email per student containing all eligible unreturned books. Reminders start three days before each due date, then continue daily while overdue; a book returned on time is not included. To send them automatically, create a daily task in **Task Scheduler**:

1. Create a Basic Task and choose a **Daily** trigger and a time when the computer is on.
2. Set **Program/script** to the project's `.venv\Scripts\python.exe`.
3. Set **Add arguments** to `-m flask --app app send-due-reminders`.
4. Set **Start in** to the project folder containing `app.py`.
5. Ensure the Windows user running the task has the `MAIL_USERNAME`, `MAIL_PASSWORD`, and `MAIL_DEFAULT_SENDER` environment variables configured. In Task Scheduler, prevent a new instance from starting while a previous instance is running.

You can run the same command manually from the project folder to send that day's reminders:

```powershell
.\.venv\Scripts\python.exe -m flask --app app send-due-reminders
```

The command records successful sends and will not resend the same loan reminder twice on the same date. Email delivery requires valid Gmail SMTP credentials and an internet connection.

In VS Code, select the `.venv` interpreter using **Python: Select Interpreter** if the editor does not detect it automatically. To stop the server, focus its terminal and press `Ctrl+C`.

## Run tests

Activate the virtual environment, then run:

```powershell
python -m pytest
```

Tests use an in-memory SQLite database and disable CSRF only for the test client. CSRF protection remains enabled in the application.

## Main workflows

- Students register with their name, unique student ID in the `256CSE145041` format (3 digits, 3 letters, 6 digits), email, and password; passwords are stored as Werkzeug hashes.
- Student registrations remain pending until the six-digit email verification code is confirmed.
- Students browse and search the public catalogue, sign in, and view their account details plus their own loan history, issue dates, due dates, return dates, and due/overdue status.
- Librarians add, edit, search, and delete catalogue entries. A book with an active loan cannot be deleted, and total stock cannot be reduced below the number of active loans.
- Librarians issue an available copy to an active student. The default due date is 14 days after issue; issuing atomically reduces available stock. They can update an active loan's due date from the loan register, but not to a date before the issue date.
- Librarians record returns. A return is accepted once and restores one copy. The estimated late fine is Rs. 10 per day after the due date; the estimate is informational and is not a payment transaction.
- The librarian loan register summarizes estimated fines by student for currently overdue books; returned loans and payments are not included in that outstanding estimate.
- Librarians can pause or reactivate student accounts and review all active, overdue, and returned loans.
- The daily `send-due-reminders` command emails students about books due within three days and continues daily for overdue books until they are returned.

## Data and configuration

The default SQLite database is `instance/smart_library.db`; the `instance` folder is excluded from Git. To use another database, set `DATABASE_URL` before running Flask. Set a stable, high-entropy `SECRET_KEY` in the environment for deployments. For a production HTTPS deployment, set `SESSION_COOKIE_SECURE=true` and use a production WSGI server; Flask's debug server is for local development only.

The app creates its tables only when `flask --app app init-db` is run; it does not reset or erase existing data at startup. Back up the SQLite database file before making manual changes.
