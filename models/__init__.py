from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func

db = SQLAlchemy()

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
    start_time = db.Column(db.DateTime, nullable=False) 
    end_time = db.Column(db.DateTime, nullable=False)   
    participant_info = db.Column(db.JSON, nullable=True)
    requested_equipment = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # Soft Delete 및 거절 사유 컬럼
    is_deleted = db.Column(db.Boolean, nullable=False, default=False)
    reject_reason = db.Column(db.String(255), nullable=True)

    def __repr__(self):
        return f'<Reservation {self.id} {self.start_time}~{self.end_time}>'