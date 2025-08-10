from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.sql import func
from werkzeug.security import generate_password_hash, check_password_hash

# SQLAlchemy 인스턴스 생성
db = SQLAlchemy()

# 사용자 모델 (Users Table)
class User(db.Model):
    """
    사용자 정보를 저장하는 모델
    - id: 기본 키
    - email: 사용자 이메일 (로그인 시 사용)
    - password: 해시된 비밀번호
    - username: 사용자 이름
    - phone_number: 연락처
    - user_type: 사용자 유형 ('general' 또는 'admin')
    - social_id: 소셜 로그인 시 사용되는 ID
    - created_at: 계정 생성일
    """
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(80), nullable=False)
    phone_number = db.Column(db.String(20), nullable=True)
    user_type = db.Column(db.String(10), nullable=False, default='general')
    social_id = db.Column(db.String(255), nullable=True, unique=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    # User 모델과 Reservation 모델 간의 관계 설정
    # 'reservations'를 통해 사용자가 한 모든 예약을 불러올 수 있음
    reservations = db.relationship('Reservation', backref='user', lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f'<User {self.username}>'

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)


# 시설 모델 (Facilities Table)
class Facility(db.Model):
    """
    대관 가능한 시설 정보를 저장하는 모델
    - id: 기본 키
    - name: 시설 이름 (예: 밴드 연습실)
    - type: 시설 유형 (예: 연습실, 회의실)
    - capacity: 수용 가능 인원
    - description: 시설 상세 설명
    - image_url: 시설 대표 이미지 URL
    - created_at: 시설 정보 등록일
    """
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


# 예약 모델 (Reservations Table)
class Reservation(db.Model):
    """
    예약 정보를 저장하는 모델
    - id: 기본 키
    - user_id: 예약을 신청한 사용자 ID (비회원일 경우 NULL)
    - facility_id: 예약된 시설 ID
    - status: 예약 상태 ('pending', 'approved', 'rejected', 'canceled')
    - start_time, end_time: 예약 시작 및 종료 시간
    - activity_details: 활동 내용
    - participant_info: 이용 인원 정보 (JSON)
    - requested_equipment: 필요 물품 정보 (JSON)
    - non_member_name, non_member_contact: 비회원 예약자 정보
    - created_at: 예약 신청일
    """
    __tablename__ = 'reservations'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    facility_id = db.Column(db.Integer, db.ForeignKey('facilities.id', ondelete='CASCADE'), nullable=False)
    status = db.Column(db.String(10), nullable=False, default='pending')
    start_time = db.Column(db.DateTime, nullable=False)
    end_time = db.Column(db.DateTime, nullable=False)
    activity_details = db.Column(db.Text, nullable=True)
    participant_info = db.Column(db.JSON, nullable=True)
    requested_equipment = db.Column(db.JSON, nullable=True)
    non_member_name = db.Column(db.String(100), nullable=True)
    non_member_contact = db.Column(db.String(20), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), server_default=func.now())

    def __repr__(self):
        return f'<Reservation {self.id} for Facility {self.facility_id}>'