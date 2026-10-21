from datetime import date, datetime, timezone

from flask_login import UserMixin
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    full_name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(254), unique=True, nullable=False, index=True)
    student_id = db.Column(db.String(40), unique=True, nullable=True, index=True)
    department = db.Column(db.String(100), nullable=True)
    password_hash = db.Column(db.String(256), nullable=False)
    role = db.Column(db.String(20), nullable=False, default="student", index=True)
    active = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    loans = db.relationship("Loan", back_populates="user", lazy="select")

    @property
    def is_active(self):
        return self.active

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


class Book(db.Model):
    __tablename__ = "books"
    __table_args__ = (
        CheckConstraint("total_copies >= 0", name="ck_books_total_nonnegative"),
        CheckConstraint("available_copies >= 0", name="ck_books_available_nonnegative"),
        CheckConstraint(
            "available_copies <= total_copies", name="ck_books_available_lte_total"
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(180), nullable=False, index=True)
    author = db.Column(db.String(120), nullable=False, index=True)
    isbn = db.Column(db.String(32), unique=True, nullable=True, index=True)
    category = db.Column(db.String(80), nullable=False, index=True)
    total_copies = db.Column(db.Integer, nullable=False, default=1)
    available_copies = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    loans = db.relationship("Loan", back_populates="book", lazy="select")


class Loan(db.Model):
    __tablename__ = "loans"
    __table_args__ = (
        CheckConstraint("due_date >= issue_date", name="ck_loans_due_after_issue"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    book_id = db.Column(db.Integer, db.ForeignKey("books.id", ondelete="SET NULL"), nullable=True, index=True)
    book_title = db.Column(db.String(180), nullable=False)
    issue_date = db.Column(db.Date, nullable=False, default=date.today)
    due_date = db.Column(db.Date, nullable=False)
    return_date = db.Column(db.Date, nullable=True, index=True)
    user = db.relationship("User", back_populates="loans")
    book = db.relationship("Book", back_populates="loans")

    @property
    def is_overdue(self):
        return self.return_date is None and self.due_date < date.today()

    @property
    def fine_amount(self):
        end_date = self.return_date or date.today()
        late_days = max((end_date - self.due_date).days, 0)
        return late_days * 2
