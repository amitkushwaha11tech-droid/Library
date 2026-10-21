import hashlib
import re
import smtplib
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from functools import wraps
from urllib.parse import urlparse

import click
from flask import Flask, abort, current_app, flash, redirect, render_template, request, url_for
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from flask_wtf.csrf import CSRFProtect
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, or_, update
from sqlalchemy.exc import IntegrityError

from config import Config
from models import Book, Loan, User, db


login_manager = LoginManager()
login_manager.login_view = "login"
login_manager.login_message = "Please sign in to continue."
login_manager.login_message_category = "info"
csrf = CSRFProtect()


def send_password_reset_email(recipient, reset_url):
    config = current_app.config
    sender = config.get("MAIL_DEFAULT_SENDER") or config.get("MAIL_USERNAME")
    username = config.get("MAIL_USERNAME")
    password = config.get("MAIL_PASSWORD")
    if not sender or not username or not password:
        raise RuntimeError("Email delivery is not configured")

    message = EmailMessage()
    message["Subject"] = "Reset your Smart Library password"
    message["From"] = sender
    message["To"] = recipient
    message.set_content(
        "We received a request to reset your Smart Library password.\n\n"
        f"Use this link within {config['PASSWORD_RESET_TIMEOUT'] // 60} minutes:\n"
        f"{reset_url}\n\n"
        "If you did not request this, you can ignore this email."
    )

    with smtplib.SMTP(config["MAIL_SERVER"], config["MAIL_PORT"], timeout=20) as smtp:
        if config["MAIL_USE_TLS"]:
            smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(message)


def create_app(test_config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    with app.app_context():
        db.create_all()

    @app.context_processor
    def inject_template_globals():
        return {"now": datetime.now(timezone.utc)}

    @login_manager.user_loader
    def load_user(user_id):
        return db.session.get(User, int(user_id))

    def admin_required(view):
        @wraps(view)
        @login_required
        def wrapped_view(*args, **kwargs):
            if current_user.role != "admin":
                abort(403)
            return view(*args, **kwargs)

        return wrapped_view

    def valid_email(email):
        return bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email or ""))

    def book_search(query):
        books = Book.query
        if query:
            pattern = f"%{query}%"
            books = books.filter(
                or_(
                    Book.title.ilike(pattern),
                    Book.author.ilike(pattern),
                    Book.isbn.ilike(pattern),
                    Book.category.ilike(pattern),
                )
            )
        return books.order_by(Book.title.asc())

    @app.get("/")
    def index():
        query = request.args.get("q", "").strip()[:100]
        books = book_search(query).limit(6).all()
        return render_template("index.html", books=books, query=query)

    @app.get("/books")
    def books():
        query = request.args.get("q", "").strip()[:100]
        pagination = book_search(query).paginate(
            page=request.args.get("page", 1, type=int), per_page=12, error_out=False
        )
        return render_template(
            "books.html", pagination=pagination, query=query, admin_mode=False
        )

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            full_name = request.form.get("full_name", "").strip()
            student_id = request.form.get("student_id", "").strip()
            department = request.form.get("department", "").strip()
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")
            errors = []
            if not full_name or len(full_name) > 120:
                errors.append("Enter your name (up to 120 characters).")
            if not student_id or len(student_id) > 40:
                errors.append("Enter a valid student ID (up to 40 characters).")
            if not valid_email(email) or len(email) > 254:
                errors.append("Enter a valid email address.")
            if len(department) > 100:
                errors.append("Department must be 100 characters or fewer.")
            if len(password) < 8 or len(password) > 128:
                errors.append("Password must be between 8 and 128 characters.")
            if password != confirm_password:
                errors.append("The passwords do not match.")
            if User.query.filter_by(email=email).first():
                errors.append("An account with that email already exists.")
            if User.query.filter_by(student_id=student_id).first():
                errors.append("That student ID is already registered.")
            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                user = User(
                    full_name=full_name,
                    student_id=student_id,
                    department=department or None,
                    email=email,
                    role="student",
                )
                user.set_password(password)
                db.session.add(user)
                try:
                    db.session.commit()
                except IntegrityError:
                    db.session.rollback()
                    flash("That email or student ID is already registered.", "error")
                else:
                    flash("Your student account is ready. Sign in to continue.", "success")
                    return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if current_user.is_authenticated:
            return redirect(url_for("dashboard"))
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            password = request.form.get("password", "")
            user = User.query.filter_by(email=email).first()
            if user and user.is_active and user.check_password(password):
                login_user(user, remember=request.form.get("remember") == "on")
                next_url = request.args.get("next", "")
                parsed_next_url = urlparse(next_url)
                if (
                    next_url.startswith("/")
                    and not next_url.startswith("//")
                    and "\\" not in next_url
                    and not parsed_next_url.scheme
                    and not parsed_next_url.netloc
                ):
                    return redirect(next_url)
                return redirect(url_for("dashboard"))
            flash("Email or password was not recognized.", "error")
        return render_template("login.html")

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            email = request.form.get("email", "").strip().lower()
            user = User.query.filter_by(email=email).first()
            if user:
                serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
                token = serializer.dumps(
                    {
                        "user_id": user.id,
                        "password_hash_digest": hashlib.sha256(
                            user.password_hash.encode("utf-8")
                        ).hexdigest(),
                    },
                    salt="password-reset",
                )
                reset_url = url_for("reset_password", token=token, _external=True)
                try:
                    send_password_reset_email(user.email, reset_url)
                except Exception:
                    current_app.logger.exception("Could not send password reset email")
            flash(
                "If an account exists for that email, a password reset link has been sent.",
                "info",
            )
            return redirect(url_for("forgot_password"))
        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"])
        try:
            payload = serializer.loads(
                token,
                salt="password-reset",
                max_age=app.config["PASSWORD_RESET_TIMEOUT"],
            )
        except (BadSignature, SignatureExpired):
            flash("This password reset link is invalid or has expired.", "error")
            return redirect(url_for("forgot_password"))

        user = db.session.get(User, payload.get("user_id"))
        current_hash_digest = (
            hashlib.sha256(user.password_hash.encode("utf-8")).hexdigest()
            if user
            else None
        )
        if not user or payload.get("password_hash_digest") != current_hash_digest:
            flash("This password reset link is invalid or has expired.", "error")
            return redirect(url_for("forgot_password"))

        if request.method == "POST":
            password = request.form.get("password", "")
            confirm_password = request.form.get("confirm_password", "")
            if len(password) < 8 or len(password) > 128:
                flash("Password must be between 8 and 128 characters.", "error")
            elif password != confirm_password:
                flash("The passwords do not match.", "error")
            else:
                user.set_password(password)
                db.session.commit()
                flash("Your password has been changed. Sign in to continue.", "success")
                return redirect(url_for("login"))
        return render_template("reset_password.html")

    @app.post("/logout")
    @login_required
    def logout():
        logout_user()
        flash("You have been signed out.", "success")
        return redirect(url_for("index"))

    @app.get("/dashboard")
    @login_required
    def dashboard():
        if current_user.role == "admin":
            return redirect(url_for("admin_dashboard"))
        return redirect(url_for("student_dashboard"))

    @app.get("/student/dashboard")
    @login_required
    def student_dashboard():
        if current_user.role != "student":
            abort(403)
        loans = Loan.query.filter_by(user_id=current_user.id).order_by(
            Loan.return_date.is_not(None), Loan.due_date.asc()
        ).all()
        active_loans = [loan for loan in loans if loan.return_date is None]
        due_soon_count = sum(
            1 for loan in active_loans
            if 0 <= (loan.due_date - date.today()).days <= 3
        )
        return render_template(
            "student_dashboard.html",
            loans=loans,
            active_loans=active_loans,
            due_soon_count=due_soon_count,
        )

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        today = date.today()
        stats = {
            "books": Book.query.count(),
            "copies": db.session.query(func.coalesce(func.sum(Book.total_copies), 0)).scalar(),
            "students": User.query.filter_by(role="student").count(),
            "active_loans": Loan.query.filter_by(return_date=None).count(),
            "overdue": Loan.query.filter(
                Loan.return_date.is_(None), Loan.due_date < today
            ).count(),
        }
        recent_loans = Loan.query.order_by(Loan.issue_date.desc(), Loan.id.desc()).limit(6).all()
        return render_template(
            "admin_dashboard.html", stats=stats, recent_loans=recent_loans
        )

    @app.get("/admin/books")
    @admin_required
    def admin_books():
        query = request.args.get("q", "").strip()[:100]
        pagination = book_search(query).paginate(
            page=request.args.get("page", 1, type=int), per_page=15, error_out=False
        )
        return render_template(
            "books.html", pagination=pagination, query=query, admin_mode=True
        )

    def parse_book_form(book=None):
        title = request.form.get("title", "").strip()
        author = request.form.get("author", "").strip()
        isbn = request.form.get("isbn", "").strip() or None
        category = request.form.get("category", "").strip()
        raw_total = request.form.get("total_copies", "")
        errors = []
        if not title or len(title) > 180:
            errors.append("Title is required and must be 180 characters or fewer.")
        if not author or len(author) > 120:
            errors.append("Author is required and must be 120 characters or fewer.")
        if isbn and len(isbn) > 32:
            errors.append("ISBN must be 32 characters or fewer.")
        if not category or len(category) > 80:
            errors.append("Category is required and must be 80 characters or fewer.")
        try:
            total_copies = int(raw_total)
            if not 0 <= total_copies <= 100000:
                raise ValueError
        except (TypeError, ValueError):
            total_copies = 0
            errors.append("Total copies must be a whole number from 0 to 100,000.")
        if isbn:
            duplicate = Book.query.filter(Book.isbn == isbn)
            if book:
                duplicate = duplicate.filter(Book.id != book.id)
            if duplicate.first():
                errors.append("That ISBN is already in the catalogue.")
        if book:
            active_count = Loan.query.filter_by(book_id=book.id, return_date=None).count()
            if total_copies < active_count:
                errors.append(
                    f"Total copies cannot be less than the {active_count} currently issued."
                )
        return {
            "title": title,
            "author": author,
            "isbn": isbn,
            "category": category,
            "total_copies": total_copies,
        }, errors

    @app.route("/admin/books/new", methods=["GET", "POST"])
    @admin_required
    def add_book():
        if request.method == "POST":
            values, errors = parse_book_form()
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("book_form.html", book=None)
            book = Book(**values, available_copies=values["total_copies"])
            db.session.add(book)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("That ISBN is already in the catalogue.", "error")
                return render_template("book_form.html", book=None)
            flash("Book added to the catalogue.", "success")
            return redirect(url_for("admin_books"))
        return render_template("book_form.html", book=None)

    @app.route("/admin/books/<int:book_id>/edit", methods=["GET", "POST"])
    @admin_required
    def edit_book(book_id):
        book = db.get_or_404(Book, book_id)
        if request.method == "POST":
            values, errors = parse_book_form(book)
            if errors:
                for error in errors:
                    flash(error, "error")
                return render_template("book_form.html", book=book)
            active_count = Loan.query.filter_by(book_id=book.id, return_date=None).count()
            for field, value in values.items():
                setattr(book, field, value)
            book.available_copies = values["total_copies"] - active_count
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("That ISBN is already in the catalogue.", "error")
                return render_template("book_form.html", book=book)
            flash("Book details updated.", "success")
            return redirect(url_for("admin_books"))
        return render_template("book_form.html", book=book)

    @app.post("/admin/books/<int:book_id>/delete")
    @admin_required
    def delete_book(book_id):
        book = db.get_or_404(Book, book_id)
        if Loan.query.filter_by(book_id=book.id, return_date=None).first():
            flash("This book has active loans and cannot be deleted.", "error")
        else:
            for loan in book.loans:
                loan.book_id = None
            db.session.delete(book)
            db.session.commit()
            flash("Book removed. Historical loan records were retained.", "success")
        return redirect(url_for("admin_books"))

    @app.get("/admin/students")
    @admin_required
    def students():
        query = request.args.get("q", "").strip()[:100]
        student_query = User.query.filter_by(role="student")
        if query:
            pattern = f"%{query}%"
            student_query = student_query.filter(
                or_(
                    User.full_name.ilike(pattern),
                    User.email.ilike(pattern),
                    User.student_id.ilike(pattern),
                    User.department.ilike(pattern),
                )
            )
        students_list = student_query.order_by(User.full_name.asc()).all()
        active_loan_counts = dict(
            db.session.query(Loan.user_id, func.count(Loan.id))
            .filter(Loan.return_date.is_(None))
            .group_by(Loan.user_id)
            .all()
        )
        return render_template(
            "students.html",
            students=students_list,
            query=query,
            active_loan_counts=active_loan_counts,
        )

    @app.post("/admin/students/<int:user_id>/toggle")
    @admin_required
    def toggle_student(user_id):
        student = db.get_or_404(User, user_id)
        if student.role != "student":
            abort(404)
        student.active = not student.active
        db.session.commit()
        flash(
            f"{student.full_name}'s account is now {'active' if student.active else 'paused'}.",
            "success",
        )
        return redirect(url_for("students"))

    @app.route("/admin/issue", methods=["GET", "POST"])
    @admin_required
    def issue_book():
        if request.method == "POST":
            student = db.session.get(User, request.form.get("student_id", type=int))
            book_id = request.form.get("book_id", type=int)
            book = db.session.get(Book, book_id) if book_id else None
            due_input = request.form.get("due_date", "").strip()
            due_date = date.today() + timedelta(days=14)
            errors = []
            if not student or student.role != "student" or not student.is_active:
                errors.append("Select an active registered student.")
            if not book:
                errors.append("Select a book from the catalogue.")
            if due_input:
                try:
                    due_date = date.fromisoformat(due_input)
                    if due_date < date.today():
                        raise ValueError
                except ValueError:
                    errors.append("Due date must be today or later.")
            if errors:
                for error in errors:
                    flash(error, "error")
            else:
                stock_update = db.session.execute(
                    update(Book)
                    .where(Book.id == book.id, Book.available_copies > 0)
                    .values(available_copies=Book.available_copies - 1)
                )
                if stock_update.rowcount != 1:
                    db.session.rollback()
                    flash("That title is no longer available to issue.", "error")
                else:
                    db.session.add(
                        Loan(
                            user_id=student.id,
                            book_id=book.id,
                            book_title=book.title,
                            issue_date=date.today(),
                            due_date=due_date,
                        )
                    )
                    try:
                        db.session.commit()
                    except IntegrityError:
                        db.session.rollback()
                        flash("The loan could not be recorded. Please try again.", "error")
                    else:
                        flash(f"{book.title} issued to {student.full_name}.", "success")
                        return redirect(url_for("loans"))
        available_books = Book.query.filter(Book.available_copies > 0).order_by(Book.title).all()
        student_options = User.query.filter_by(role="student", active=True).order_by(
            User.full_name
        ).all()
        return render_template(
            "issue_book.html", books=available_books, students=student_options
        )

    @app.get("/admin/loans")
    @admin_required
    def loans():
        status = request.args.get("status", "all")
        loan_query = Loan.query
        if status == "active":
            loan_query = loan_query.filter(Loan.return_date.is_(None))
        elif status == "returned":
            loan_query = loan_query.filter(Loan.return_date.is_not(None))
        elif status == "overdue":
            loan_query = loan_query.filter(
                Loan.return_date.is_(None), Loan.due_date < date.today()
            )
        loan_list = loan_query.order_by(Loan.return_date.is_(None).desc(), Loan.due_date.asc()).all()
        return render_template("loans.html", loans=loan_list, status=status)

    @app.post("/admin/loans/<int:loan_id>/return")
    @admin_required
    def return_book(loan_id):
        loan = db.get_or_404(Loan, loan_id)
        if loan.return_date is not None:
            flash("This book has already been returned.", "info")
        else:
            result = db.session.execute(
                update(Loan)
                .where(Loan.id == loan.id, Loan.return_date.is_(None))
                .values(return_date=date.today())
            )
            if result.rowcount != 1:
                db.session.rollback()
                flash("This loan has already been returned.", "info")
            else:
                fine = loan.fine_amount
                if loan.book_id:
                    db.session.execute(
                        update(Book)
                        .where(Book.id == loan.book_id)
                        .values(available_copies=Book.available_copies + 1)
                    )
                db.session.commit()
                flash(f"Book returned. Estimated late fine: Rs. {fine}.", "success")
        return redirect(url_for("loans"))

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("403.html"), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("404.html"), 404

    @app.cli.command("init-db")
    def init_db_command():
        """Create the database tables if they do not exist."""
        db.create_all()
        click.echo("Database tables are ready.")

    @app.cli.command("create-admin")
    def create_admin_command():
        """Create an administrator account without exposing its password in source."""
        email = click.prompt("Administrator email").strip().lower()
        if not valid_email(email):
            raise click.ClickException("Enter a valid email address.")
        if User.query.filter_by(email=email).first():
            raise click.ClickException("An account with this email already exists.")
        full_name = click.prompt("Administrator name").strip()
        if not full_name or len(full_name) > 120:
            raise click.ClickException("Name must be between 1 and 120 characters.")
        password = click.prompt("Password (minimum 12 characters)", hide_input=True)
        confirmation = click.prompt("Confirm password", hide_input=True)
        if len(password) < 12 or len(password) > 128:
            raise click.ClickException("Password must be between 12 and 128 characters.")
        if password != confirmation:
            raise click.ClickException("Passwords do not match.")
        admin = User(full_name=full_name, email=email, role="admin")
        admin.set_password(password)
        db.session.add(admin)
        db.session.commit()
        click.echo(f"Administrator account created for {email}.")

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
