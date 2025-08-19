from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash, check_password_hash

# SQLAlchemy 인스턴스 생성
db = SQLAlchemy()

# 관리자 모델 (Admin Table) - NEW
class Admin(db.Model):
    """
    관리자 계정 정보를 저장하는 모델
    - id: 기본 키
    - username: 관리자 로그인 ID
    - password: 해시된 비밀번호
    """
    __tablename__ = 'admins'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)

    def __repr__(self):
        return f'<Admin {self.username}>'

    def set_password(self, password):
        """비밀번호를 해시하여 저장"""
        self.password = generate_password_hash(password)

    def check_password(self, password):
        """입력된 비밀번호와 저장된 해시 비밀번호를 비교"""
        return check_password_hash(self.password, password)


# 시설 모델 (Facilities Table) - UNCHANGED
class Facility(db.Model):
    __tablename__ = 'facilities'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    type = db.Column(db.String(50), nullable=False)
    capacity = db.Column(db.Integer, nullable=True)
    description = db.Column(db.Text, nullable=True)
    image_url = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # Facility 모델과 Reservation 모델 간의 관계 설정
    reservations = db.relationship('Reservation', backref='facility', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<Facility {self.name}>'


# 예약 모델 (Reservations Table) - MODIFIED
class Reservation(db.Model):
    """
    예약 정보를 저장하는 모델
    - User와의 관계가 제거되고, 모든 신청자 정보를 직접 저장합니다.
    """
    __tablename__ = 'reservations'

    id = db.Column(db.Integer, primary_key=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facilities.id', ondelete='CASCADE'), nullable=False)
    
    # 신청자 정보 컬럼 (기존 non_member 필드를 확장)
    applicant_name = db.Column(db.String(100), nullable=False)
    applicant_contact = db.Column(db.String(20), nullable=False)
    applicant_school = db.Column(db.String(100), nullable=True)
    applicant_club = db.Column(db.String(100), nullable=True)

    # 예약 관련 정보
    status = db.Column(db.String(30), nullable=False, default='pending')
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    activity_details = db.Column(db.Text, nullable=True)
    participant_info = db.Column(db.JSON, nullable=True)
    requested_equipment = db.Column(db.JSON, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f'<Reservation {self.id} by {self.applicant_name}>'

