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

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "type": self.type,
            "capacity": self.capacity,
            "description": self.description,
            "image_url": self.image_url,
        }

class Reservation(db.Model):
    __tablename__ = 'reservations'
    id = db.Column(db.Integer, primary_key=True)

    access_id = db.Column(db.String(36), unique=True, nullable=True)
    
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

    def to_dict(self, include_facility=True):
        data = {
            "id": self.id,
            "access_id": self.access_id,
            "facility_id": self.facility_id,
            "applicant_name": self.applicant_name,
            "applicant_contact": self.applicant_contact,
            "applicant_school": self.applicant_school,
            "applicant_club": self.applicant_club,
            "status": self.status,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "end_time": self.end_time.isoformat() if self.end_time else None,
            "participant_info": self.participant_info or {},
            "requested_equipment": self.requested_equipment or [],
            "is_deleted": self.is_deleted,
            "reject_reason": self.reject_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_facility and self.facility is not None:
            data["facility"] = self.facility.to_dict()
        return data