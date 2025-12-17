from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash, check_password_hash

db = SQLAlchemy()

class Admin(db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

class Facility(db.Model):
    __tablename__ = 'facilities'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    capacity = db.Column(db.Integer, nullable=True)
    description = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())
    reservations = db.relationship('Reservation', backref='facility', lazy=True, cascade="all, delete-orphan")

class Reservation(db.Model):
    __tablename__ = 'reservations'
    id = db.Column(db.Integer, primary_key=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facilities.id', ondelete='CASCADE'), nullable=False)
    
    applicant_name = db.Column(db.String(100), nullable=False)
    applicant_contact = db.Column(db.String(20), nullable=False)
    applicant_school = db.Column(db.String(100), nullable=True)
    applicant_club = db.Column(db.String(100), nullable=True)

    status = db.Column(db.String(30), nullable=False, default='pending')
    start_time = db.Column(db.DateTime, nullable=False) # 예약 시작 시간 (년-월-일 시:분:초)
    end_time = db.Column(db.DateTime, nullable=False)   # 예약 종료 시간
    participant_info = db.Column(db.JSON, nullable=True)
    requested_equipment = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f'<Reservation {self.id} {self.start_time}~{self.end_time}>'