from datetime import date, timedelta
from urllib.parse import urlparse

import pytest

import app as app_module
from app import create_app
from models import Book, DueReminderLog, Loan, PendingRegistration, User, db


@pytest.fixture

def app():
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test-secret-key",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
            "WTF_CSRF_ENABLED": False,
        }
    )
    with application.app_context():
        db.create_all()
        yield application
        db.session.remove()
        db.drop_all()


@pytest.fixture

def client(app):
    return app.test_client()


def make_user(email, role="student", name="Test User", student_id=None):
    user = User(
        full_name=name,
        email=email,
        role=role,
        student_id=student_id,
    )
    user.set_password("test-password-123")
    db.session.add(user)
    db.session.commit()
    return user


def sign_in(client, email):
    return client.post(
        "/login",
        data={"email": email, "password": "test-password-123"},
        follow_redirects=True,
    )


def test_public_registration_requires_email_otp_and_cannot_create_admin(
    client, app, monkeypatch
):
    sent_emails = []
    monkeypatch.setattr(
        app_module, "send_email", lambda recipient, subject, body: sent_emails.append(
            (recipient, subject, body)
        )
    )
    monkeypatch.setattr(app_module.secrets, "randbelow", lambda _limit: 123456)
    response = client.post(
        "/register",
        data={
            "full_name": "New Student",
            "student_id": "256CSE145100",
            "department": "CST",
            "email": "student@gmail.com",
            "password": "strong-password-1",
            "confirm_password": "strong-password-1",
            "role": "admin",
        },
    )

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/verify-email")
    assert sent_emails[0][0] == "student@gmail.com"
    assert "123456" in sent_emails[0][2]
    with app.app_context():
        assert User.query.filter_by(email="student@gmail.com").count() == 0
        pending = PendingRegistration.query.filter_by(email="student@gmail.com").one()
        assert pending.student_id == "256CSE145100"

    verified = client.post("/verify-email", data={"otp": "123456"})
    assert verified.status_code == 302
    assert verified.headers["Location"].endswith("/login")
    with app.app_context():
        student = User.query.filter_by(email="student@gmail.com").one()
        assert student.role == "student"
        assert student.password_hash != "strong-password-1"
        assert student.check_password("strong-password-1")
        assert PendingRegistration.query.count() == 0


def test_student_registration_rejects_non_gmail_addresses(client):
    response = client.post(
        "/register",
        data={
            "full_name": "New Student",
            "student_id": "256CSE145101",
            "department": "CST",
            "email": "student@example.edu",
            "password": "strong-password-1",
            "confirm_password": "strong-password-1",
        },
    )

    assert response.status_code == 200
    assert b"Use a student Gmail ID ending in @gmail.com." in response.data


@pytest.mark.parametrize(
    "student_id",
    ["anything", "CST-123", "26CSE145", "256CSE14505", "256CSE1450410"],
)
def test_student_registration_rejects_invalid_student_id_format(client, student_id):
    response = client.post(
        "/register",
        data={
            "full_name": "New Student",
            "student_id": student_id,
            "department": "CST",
            "email": "student@gmail.com",
            "password": "strong-password-1",
            "confirm_password": "strong-password-1",
        },
    )

    assert response.status_code == 200
    assert b"Enter a student ID in the format 256CSE145041." in response.data


def test_student_registration_normalizes_student_id_to_uppercase(
    client, app, monkeypatch
):
    monkeypatch.setattr(app_module, "send_email", lambda *_args: None)
    monkeypatch.setattr(app_module.secrets, "randbelow", lambda _limit: 654321)
    response = client.post(
        "/register",
        data={
            "full_name": "New Student",
            "student_id": "256cse145103",
            "department": "CST",
            "email": "student1003@gmail.com",
            "password": "strong-password-1",
            "confirm_password": "strong-password-1",
        },
    )

    assert response.status_code == 302
    verified = client.post("/verify-email", data={"otp": "654321"})
    assert verified.status_code == 302
    with app.app_context():
        student = User.query.filter_by(email="student1003@gmail.com").one()
        assert student.student_id == "256CSE145103"


def test_registration_rejects_incorrect_email_otp(client, app, monkeypatch):
    monkeypatch.setattr(app_module, "send_email", lambda *_args: None)
    monkeypatch.setattr(app_module.secrets, "randbelow", lambda _limit: 123456)
    client.post(
        "/register",
        data={
            "full_name": "New Student",
            "student_id": "256CSE145111",
            "department": "CST",
            "email": "student1011@gmail.com",
            "password": "strong-password-1",
            "confirm_password": "strong-password-1",
        },
    )

    response = client.post(
        "/verify-email", data={"otp": "000000"}, follow_redirects=True
    )

    assert response.status_code == 200
    assert b"That verification code is incorrect." in response.data
    with app.app_context():
        assert User.query.filter_by(email="student1011@gmail.com").count() == 0


def test_resubmitting_pending_registration_resends_otp_after_cooldown(
    client, app, monkeypatch
):
    sent_emails = []
    monkeypatch.setattr(
        app_module,
        "send_email",
        lambda recipient, subject, body: sent_emails.append((recipient, subject, body)),
    )
    codes = iter((111111, 222222))
    monkeypatch.setattr(app_module.secrets, "randbelow", lambda _limit: next(codes))
    registration = {
        "full_name": "Pending Student",
        "student_id": "256CSE145112",
        "department": "CST",
        "email": "pending112@gmail.com",
        "password": "strong-password-1",
        "confirm_password": "strong-password-1",
    }

    first_response = client.post("/register", data=registration)
    assert first_response.status_code == 302
    assert len(sent_emails) == 1

    with app.app_context():
        pending = PendingRegistration.query.filter_by(
            email="pending112@gmail.com"
        ).one()
        pending.last_sent_at -= timedelta(seconds=61)
        db.session.commit()

    retry_response = client.post("/register", data=registration)

    assert retry_response.status_code == 302
    assert retry_response.headers["Location"].endswith("/verify-email")
    assert len(sent_emails) == 2
    assert "222222" in sent_emails[1][2]
    with app.app_context():
        assert PendingRegistration.query.filter_by(
            email="pending112@gmail.com"
        ).count() == 1
        assert User.query.filter_by(email="pending112@gmail.com").count() == 0


def test_student_cannot_access_admin_routes_or_other_student_loans(client, app):
    with app.app_context():
        student = make_user("one@example.edu", student_id="CST-1")
        other_student = make_user("two@example.edu", student_id="CST-2", name="Other Student")
        book = Book(
            title="Private history title",
            author="Author",
            category="Computing",
            total_copies=1,
            available_copies=0,
        )
        db.session.add(book)
        db.session.flush()
        db.session.add(
            Loan(
                user_id=other_student.id,
                book_id=book.id,
                book_title=book.title,
                issue_date=date.today(),
                due_date=date.today() + timedelta(days=14),
            )
        )
        db.session.commit()
        student_email = student.email

    sign_in(client, student_email)
    denied = client.get("/admin")
    dashboard = client.get("/student/dashboard")

    assert denied.status_code == 403
    assert dashboard.status_code == 200
    assert b"Private history title" not in dashboard.data


def test_student_account_shows_own_book_dates_and_due_status(client, app):
    with app.app_context():
        student = make_user(
            "account-reader@example.edu",
            student_id="CST-1010",
            name="Account Reader",
        )
        student.department = "Computer Science"
        book = Book(
            title="My Borrowed Book",
            author="A. Writer",
            category="Computing",
            total_copies=1,
            available_copies=0,
        )
        db.session.add(book)
        db.session.flush()
        loan = Loan(
            user_id=student.id,
            book_id=book.id,
            book_title=book.title,
            issue_date=date.today() - timedelta(days=5),
            due_date=date.today() + timedelta(days=2),
        )
        db.session.add(loan)
        db.session.commit()
        student_email = student.email
        issue_date_label = loan.issue_date.strftime("%d %b %Y")
        due_date_label = loan.due_date.strftime("%d %b %Y")

    sign_in(client, student_email)
    response = client.get("/student/dashboard")

    assert response.status_code == 200
    assert b"My account" in response.data
    assert b"CST-1010" in response.data
    assert b"account-reader@example.edu" in response.data
    assert b"Computer Science" in response.data
    assert b"My Borrowed Book" in response.data
    assert issue_date_label.encode() in response.data
    assert due_date_label.encode() in response.data
    assert b"Due in 2 days" in response.data


def test_admin_issue_and_return_updates_stock_once(client, app):
    with app.app_context():
        admin = make_user("librarian@example.edu", role="admin", name="Librarian")
        student = make_user("reader@example.edu", student_id="CST-22", name="Reader")
        book = Book(
            title="A Library of Ideas",
            author="A. Writer",
            category="Computing",
            total_copies=1,
            available_copies=1,
        )
        db.session.add(book)
        db.session.commit()
        admin_email = admin.email
        student_id = student.id
        book_id = book.id

    sign_in(client, admin_email)
    issued = client.post(
        "/admin/issue",
        data={"student_id": student_id, "book_id": book_id},
        follow_redirects=True,
    )
    assert issued.status_code == 200
    with app.app_context():
        book = db.session.get(Book, book_id)
        loan = Loan.query.one()
        loan_id = loan.id
        assert book.available_copies == 0
        assert loan.due_date == date.today() + timedelta(days=14)

    returned = client.post(f"/admin/loans/{loan_id}/return", follow_redirects=True)
    assert returned.status_code == 200
    duplicate_return = client.post(
        f"/admin/loans/{loan_id}/return", follow_redirects=True
    )
    assert duplicate_return.status_code == 200
    with app.app_context():
        assert db.session.get(Book, book_id).available_copies == 1
        assert db.session.get(Loan, loan_id).return_date == date.today()


def test_non_admin_cannot_issue_even_with_direct_post(client, app):
    with app.app_context():
        student = make_user("student@example.edu", student_id="CST-3")
        book = Book(
            title="Protected stock",
            author="Author",
            category="Computing",
            total_copies=1,
            available_copies=1,
        )
        db.session.add(book)
        db.session.commit()
        student_email = student.email
        book_id = book.id

    sign_in(client, student_email)
    response = client.post(
        "/admin/issue",
        data={"student_id": 1, "book_id": book_id},
    )

    assert response.status_code == 403
    with app.app_context():
        assert db.session.get(Book, book_id).available_copies == 1
        assert Loan.query.count() == 0


def test_admin_can_update_active_loan_due_date_and_cannot_set_it_before_issue(client, app):
    with app.app_context():
        admin = make_user("due-date-admin@example.edu", role="admin", name="Librarian")
        student = make_user("due-date-reader@example.edu", student_id="CST-24")
        book = Book(
            title="Date Changes",
            author="A. Writer",
            category="Computing",
            total_copies=1,
            available_copies=0,
        )
        db.session.add(book)
        db.session.flush()
        loan = Loan(
            user_id=student.id,
            book_id=book.id,
            book_title=book.title,
            issue_date=date.today(),
            due_date=date.today() + timedelta(days=14),
        )
        db.session.add(loan)
        db.session.commit()
        admin_email = admin.email
        loan_id = loan.id
        original_due_date = loan.due_date

    sign_in(client, admin_email)
    updated = client.post(
        f"/admin/loans/{loan_id}/due-date",
        data={"due_date": (date.today() + timedelta(days=21)).isoformat()},
        follow_redirects=True,
    )
    assert updated.status_code == 200
    with app.app_context():
        loan = db.session.get(Loan, loan_id)
        assert loan.due_date == date.today() + timedelta(days=21)

    invalid = client.post(
        f"/admin/loans/{loan_id}/due-date",
        data={"due_date": (date.today() - timedelta(days=1)).isoformat()},
        follow_redirects=True,
    )
    assert invalid.status_code == 200
    assert b"Due date cannot be before the issue date." in invalid.data
    with app.app_context():
        assert db.session.get(Loan, loan_id).due_date == date.today() + timedelta(days=21)
        assert original_due_date < db.session.get(Loan, loan_id).due_date


def test_admin_can_manage_catalogue_and_cannot_delete_active_loan(client, app):
    with app.app_context():
        admin = make_user("catalogue@example.edu", role="admin", name="Librarian")
        admin_email = admin.email

    sign_in(client, admin_email)
    created = client.post(
        "/admin/books/new",
        data={
            "title": "Practical Computing",
            "author": "A. Example",
            "isbn": "978-1-2345-6789-0",
            "category": "Computing",
            "total_copies": "2",
        },
        follow_redirects=True,
    )
    assert created.status_code == 200

    with app.app_context():
        book = Book.query.filter_by(isbn="978-1-2345-6789-0").one()
        student = make_user("borrower@example.edu", student_id="CST-40")
        book_id = book.id
        db.session.add(
            Loan(
                user_id=student.id,
                book_id=book.id,
                book_title=book.title,
                issue_date=date.today(),
                due_date=date.today() + timedelta(days=14),
            )
        )
        book.available_copies = 1
        db.session.commit()

    blocked_delete = client.post(
        f"/admin/books/{book_id}/delete", follow_redirects=True
    )
    assert b"active loans" in blocked_delete.data
    with app.app_context():
        assert db.session.get(Book, book_id) is not None
        assert db.session.get(Book, book_id).available_copies == 1

    updated = client.post(
        f"/admin/books/{book_id}/edit",
        data={
            "title": "Practical Computing, Revised",
            "author": "A. Example",
            "isbn": "978-1-2345-6789-0",
            "category": "Computing",
            "total_copies": "3",
        },
        follow_redirects=True,
    )
    assert updated.status_code == 200
    with app.app_context():
        book = db.session.get(Book, book_id)
        assert book.title == "Practical Computing, Revised"
        assert book.available_copies == 2


def test_public_and_admin_pages_render(client, app):
    assert client.get("/").status_code == 200
    assert client.get("/books").status_code == 200
    assert client.get("/login").status_code == 200
    assert client.get("/register").status_code == 200

    with app.app_context():
        admin = make_user("pages@example.edu", role="admin", name="Librarian")
        admin_email = admin.email
    sign_in(client, admin_email)

    for path in ("/admin", "/admin/books", "/admin/students", "/admin/loans", "/admin/issue"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_admin_loan_register_shows_overdue_fine_totals_by_student(client, app):
    with app.app_context():
        admin = make_user("fine-admin@example.edu", role="admin", name="Librarian")
        student = make_user(
            "sagar@example.edu", student_id="CST-88", name="Sagar Mishra"
        )
        other_student = make_user(
            "other-fine@example.edu", student_id="CST-89", name="Other Student"
        )
        first_book = Book(
            title="First overdue book",
            author="Author",
            category="Computing",
            total_copies=1,
            available_copies=0,
        )
        second_book = Book(
            title="Second overdue book",
            author="Author",
            category="Computing",
            total_copies=1,
            available_copies=0,
        )
        returned_book = Book(
            title="Returned overdue book",
            author="Author",
            category="Computing",
            total_copies=1,
            available_copies=1,
        )
        db.session.add_all([first_book, second_book, returned_book])
        db.session.flush()
        today = date.today()
        db.session.add_all(
            [
                Loan(
                    user_id=student.id,
                    book_id=first_book.id,
                    book_title=first_book.title,
                    issue_date=today - timedelta(days=10),
                    due_date=today - timedelta(days=3),
                ),
                Loan(
                    user_id=student.id,
                    book_id=second_book.id,
                    book_title=second_book.title,
                    issue_date=today - timedelta(days=10),
                    due_date=today - timedelta(days=1),
                ),
                Loan(
                    user_id=student.id,
                    book_id=returned_book.id,
                    book_title=returned_book.title,
                    issue_date=today - timedelta(days=10),
                    due_date=today - timedelta(days=4),
                    return_date=today - timedelta(days=1),
                ),
                Loan(
                    user_id=other_student.id,
                    book_id=None,
                    book_title="Not overdue",
                    issue_date=today - timedelta(days=1),
                    due_date=today + timedelta(days=13),
                ),
            ]
        )
        db.session.commit()
        admin_email = admin.email

    sign_in(client, admin_email)
    response = client.get("/admin/loans")

    assert response.status_code == 200
    assert b"Sagar Mishra" in response.data
    assert b"Rs. 40 estimated" in response.data
    assert response.data.count(b"Other Student") == 1
    assert b"payments are not recorded here" in response.data


def test_due_reminder_command_emails_due_soon_and_overdue_once_per_day(
    app, monkeypatch
):
    sent_emails = []
    monkeypatch.setattr(
        app_module,
        "send_email",
        lambda recipient, subject, body: sent_emails.append(
            (recipient, subject, body)
        ),
    )
    with app.app_context():
        student = make_user(
            "reminder-reader@example.edu",
            student_id="CST-1020",
            name="Reminder Reader",
        )
        today = date.today()
        loans = [
            Loan(
                user_id=student.id,
                book_title="Due soon book",
                issue_date=today - timedelta(days=11),
                due_date=today + timedelta(days=3),
            ),
            Loan(
                user_id=student.id,
                book_title="Overdue book",
                issue_date=today - timedelta(days=20),
                due_date=today - timedelta(days=2),
            ),
            Loan(
                user_id=student.id,
                book_title="Outside reminder window",
                issue_date=today - timedelta(days=10),
                due_date=today + timedelta(days=4),
            ),
            Loan(
                user_id=student.id,
                book_title="Returned book",
                issue_date=today - timedelta(days=20),
                due_date=today - timedelta(days=5),
                return_date=today - timedelta(days=1),
            ),
        ]
        db.session.add_all(loans)
        db.session.commit()

    runner = app.test_cli_runner()
    first_run = runner.invoke(args=["send-due-reminders"])
    second_run = runner.invoke(args=["send-due-reminders"])

    assert first_run.exit_code == 0, first_run.output
    assert "Sent 1 due-date reminder email(s)." in first_run.output
    assert second_run.exit_code == 0, second_run.output
    assert "Sent 0 due-date reminder email(s)." in second_run.output
    assert len(sent_emails) == 1
    assert sent_emails[0][0] == "reminder-reader@example.edu"
    assert "Due soon book" in sent_emails[0][2]
    assert "Overdue book" in sent_emails[0][2]
    assert "Outside reminder window" not in sent_emails[0][2]
    assert "Returned book" not in sent_emails[0][2]
    with app.app_context():
        assert DueReminderLog.query.count() == 2


def test_password_reset_email_changes_password_and_token_is_one_time(client, app, monkeypatch):
    sent_emails = []
    monkeypatch.setattr(
        app_module,
        "send_password_reset_email",
        lambda recipient, reset_url: sent_emails.append((recipient, reset_url)),
    )
    with app.app_context():
        user = make_user("reset@example.edu")
        original_hash = user.password_hash

    response = client.post(
        "/forgot-password",
        data={"email": "RESET@example.edu"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"If an account exists" in response.data
    assert sent_emails[0][0] == "reset@example.edu"
    reset_path = urlparse(sent_emails[0][1]).path
    reset_page = client.get(reset_path)
    assert reset_page.status_code == 200
    assert b"New password" in reset_page.data

    changed = client.post(
        reset_path,
        data={"password": "new-password-456", "confirm_password": "new-password-456"},
        follow_redirects=True,
    )
    assert changed.status_code == 200
    assert b"password has been changed" in changed.data
    with app.app_context():
        user = User.query.filter_by(email="reset@example.edu").one()
        assert user.password_hash != original_hash
        assert user.check_password("new-password-456")

    reused = client.get(reset_path, follow_redirects=True)
    assert b"invalid or has expired" in reused.data


def test_password_reset_request_does_not_disclose_unknown_emails(client, monkeypatch):
    sent_emails = []
    monkeypatch.setattr(
        app_module,
        "send_password_reset_email",
        lambda recipient, reset_url: sent_emails.append((recipient, reset_url)),
    )
    response = client.post(
        "/forgot-password",
        data={"email": "missing@example.edu"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    assert b"If an account exists" in response.data
    assert sent_emails == []


def test_create_admin_cli_prompts_and_hashes_password(app):
    runner = app.test_cli_runner()
    result = runner.invoke(
        args=["create-admin"],
        input=(
            "librarian@example.edu\n"
            "Library Admin\n"
            "very-strong-admin-password\n"
            "very-strong-admin-password\n"
        ),
    )

    assert result.exit_code == 0, result.output
    with app.app_context():
        admin = User.query.filter_by(email="librarian@example.edu").one()
        assert admin.role == "admin"
        assert admin.check_password("very-strong-admin-password")


def test_csrf_rejects_registration_without_token():
    application = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "csrf-test-secret",
            "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        }
    )
    response = application.test_client().post(
        "/register",
        data={"email": "student@example.edu", "password": "strong-password-1"},
    )

    assert response.status_code == 400
