from datetime import date, timedelta
from urllib.parse import urlparse

import pytest

import app as app_module
from app import create_app
from models import Book, Loan, User, db


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


def test_public_registration_cannot_create_admin(client, app):
    response = client.post(
        "/register",
        data={
            "full_name": "New Student",
            "student_id": "CST-1001",
            "department": "CST",
            "email": "student@example.edu",
            "password": "strong-password-1",
            "confirm_password": "strong-password-1",
            "role": "admin",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    with app.app_context():
        student = User.query.filter_by(email="student@example.edu").one()
        assert student.role == "student"
        assert student.password_hash != "strong-password-1"
        assert student.check_password("strong-password-1")


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
