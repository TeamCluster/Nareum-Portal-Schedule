from flask import Flask, render_template, request, redirect, url_for, flash
from models import db, Facility, Reservation
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os, sys, io, re

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ["PYTHONIOENCODING"] = "utf-8"
load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///resv.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "secret"

SERVICE_NAME = "나름센터 활동실 대관"
db.init_app(app)

@app.route('/')
def index():
    selected_date_str = request.args.get("date")
    facilities = Facility.query.all()
    
    availability_data = {}
    is_reservable = True

    if selected_date_str:
        try:
            target_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
            today = datetime.today().date()

            if target_date <= today:
                flash("예약 신청은 내일 날짜부터 가능합니다. (현재는 현황 조회만 가능합니다)")
                is_reservable = False

            start_of_day = datetime.combine(target_date, datetime.min.time())
            end_of_day = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

            for facility in facilities:
                # [수정] 대기(pending) 중인 예약도 다른 사람이 예약하지 못하도록 자리 차지
                reservations = Reservation.query.filter(
                    Reservation.facility_id == facility.id,
                    Reservation.start_time >= start_of_day,
                    Reservation.start_time < end_of_day,
                    Reservation.status.in_(["confirmed", "pending"]) 
                ).all()

                hours_status = {h: "available" for h in range(9, 18)}

                for res in reservations:
                    s_h = res.start_time.hour
                    e_h = res.end_time.hour
                    for h in range(s_h, e_h):
                        if 9 <= h < 18:
                            hours_status[h] = "booked"
                
                availability_data[facility.id] = hours_status

        except ValueError:
            flash("날짜 형식이 올바르지 않습니다.")
            selected_date_str = None

    return render_template(
        "index.html", 
        SERVICE_NAME=SERVICE_NAME, 
        facilities=facilities,
        selected_date=selected_date_str,
        availability_data=availability_data,
        is_reservable=is_reservable
    )

@app.route('/reserve/<int:facility_id>', methods=["GET", "POST"])
def reserve(facility_id):
    selected_date_str = request.args.get("date")
    if not selected_date_str:
        flash("예약할 날짜를 먼저 선택해주세요.")
        return redirect(url_for("index"))

    selected_date_obj = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
    facility = Facility.query.get_or_404(facility_id)

    # [수정] 대기 중인 예약도 예약 불가능한 시간으로 처리
    existing_res = Reservation.query.filter(
        Reservation.facility_id == facility_id,
        Reservation.start_time >= datetime.combine(selected_date_obj, datetime.min.time()),
        Reservation.start_time < datetime.combine(selected_date_obj + timedelta(days=1), datetime.min.time()),
        Reservation.status.in_(["confirmed", "pending"])
    ).all()

    booked_hours = []
    for res in existing_res:
        s_h = res.start_time.hour
        e_h = res.end_time.hour
        for h in range(s_h, e_h):
            booked_hours.append(h)
    
    if request.method == "POST":
        name = request.form.get("name")
        contact = request.form.get("contact")
        school = request.form.get("school")
        club = request.form.get("club")
        selected_hours = request.form.getlist("time_slot")
        
        # 중복 방지 체크 시에도 pending 포함
        start_hour = sorted([int(h) for h in selected_hours])[0]
        end_hour = sorted([int(h) for h in selected_hours])[-1] + 1
        start_dt = datetime.combine(selected_date_obj, datetime.strptime(f"{start_hour:02d}:00", "%H:%M").time())
        end_dt = datetime.combine(selected_date_obj, datetime.strptime(f"{end_hour:02d}:00", "%H:%M").time())

        overlap = Reservation.query.filter(
            Reservation.facility_id == facility_id,
            Reservation.start_time < end_dt, 
            Reservation.end_time > start_dt,
            Reservation.status.in_(["confirmed", "pending"])
        ).first()

        if overlap:
            flash("선택하신 시간에 이미 다른 예약이 진행 중입니다.")
            return redirect(url_for('reserve', facility_id=facility_id, date=selected_date_str))

        # [수정] 새 예약은 기본적으로 'pending(승인 대기)' 상태로 저장
        new_res = Reservation(
            facility_id=facility_id,
            applicant_name=name,
            applicant_contact=contact,
            applicant_school=school,
            applicant_club=club,
            start_time=start_dt,
            end_time=end_dt,
            participant_info={"elementary": 0}, # 간략화 (기존 딕셔너리 로직 유지)
            status="pending" 
        )
        db.session.add(new_res)
        db.session.commit()

        return redirect(url_for("reservation_complete", res_id=new_res.id))

    return render_template("reserve.html", facility_id=facility_id, selected_date=selected_date_str, facility=facility, booked_hours=booked_hours, form_data={})

@app.route('/complete/<int:res_id>')
def reservation_complete(res_id):
    res = Reservation.query.get_or_404(res_id)
    return render_template("complete.html", reservation=res, SERVICE_NAME=SERVICE_NAME)

@app.route('/check', methods=['GET', 'POST'])
def check():
    # [수정] pending과 confirmed 모두 조회하도록 변경
    reservations = None
    search_name = search_contact = None
    if request.method == 'POST':
        search_name = request.form.get('name')
        search_contact = request.form.get('contact')
        reservations = Reservation.query.filter(
            Reservation.applicant_name == search_name,
            Reservation.applicant_contact == search_contact,
            Reservation.status.in_(["confirmed", "pending"])
        ).order_by(Reservation.start_time.desc()).all()
    return render_template('check.html', reservations=reservations, search_name=search_name, search_contact=search_contact, SERVICE_NAME=SERVICE_NAME)

@app.route('/cancel/<int:res_id>', methods=['POST'])
def cancel(res_id):
    res = Reservation.query.get_or_404(res_id)
    # 대기 중(pending)이거나 확정(confirmed)된 예약만 취소 가능
    if res.status in ['confirmed', 'pending']:
        res.status = 'cancelled'
        db.session.commit()
        flash("예약이 정상적으로 취소되었습니다.")
    else:
        flash("이미 취소되었거나 유효하지 않은 예약입니다.")
        
    return redirect(url_for('check'))

# ==========================================
# [추가] 관리자 (Admin) 라우트
# ==========================================

@app.route('/admin')
def admin():
    # 1. 승인 대기 중인 모든 예약 조회
    pending_reservations = Reservation.query.filter_by(status='pending').order_by(Reservation.start_time).all()
    
    # 2. 특정 날짜 조회 (기본값: 오늘)
    target_date_str = request.args.get('date', datetime.today().strftime('%Y-%m-%d'))
    target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
    
    start_of_day = datetime.combine(target_date, datetime.min.time())
    end_of_day = datetime.combine(target_date + timedelta(days=1), datetime.min.time())
    
    date_reservations = Reservation.query.filter(
        Reservation.start_time >= start_of_day,
        Reservation.start_time < end_of_day,
        Reservation.status != "cancelled"
    ).order_by(Reservation.start_time).all()

    return render_template('admin.html', pending_reservations=pending_reservations, date_reservations=date_reservations, target_date=target_date_str)

@app.route('/admin/approve/<int:res_id>', methods=['POST'])
def admin_approve(res_id):
    res = Reservation.query.get_or_404(res_id)
    res.status = 'confirmed'
    db.session.commit()
    flash(f"{res.applicant_name}님의 예약이 승인되었습니다.")
    return redirect(request.referrer or url_for('admin'))

@app.route('/admin/delete/<int:res_id>', methods=['POST'])
def admin_delete(res_id):
    res = Reservation.query.get_or_404(res_id)
    res.status = 'cancelled' # 삭제 대신 취소 상태로 변경 (Soft Delete)
    db.session.commit()
    flash(f"{res.applicant_name}님의 예약이 취소(삭제) 처리되었습니다.")
    return redirect(request.referrer or url_for('admin'))

@app.route('/admin/edit/<int:res_id>', methods=['GET', 'POST'])
def admin_edit(res_id):
    res = Reservation.query.get_or_404(res_id)
    if request.method == 'POST':
        res.applicant_name = request.form.get('name')
        res.applicant_contact = request.form.get('contact')
        res.applicant_school = request.form.get('school')
        res.status = request.form.get('status')
        db.session.commit()
        flash("예약 정보가 성공적으로 수정되었습니다.")
        return redirect(url_for('admin'))
        
    return render_template('admin_edit.html', res=res)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if not Facility.query.first():
            facilities = [
                Facility(name="활력충전터", type="연습실", image_url="/static/img/room001.jpg", description="밴드/음악 연습 공간"),
                Facility(name="창의키움터", type="활동실", image_url="/static/img/room002.jpg", description="과학 활동 공간"),
                Facility(name="탐구개발터", type="활동실", image_url="/static/img/room003.jpg", description="3D 프린터가 있는 오픈LAB실"),
                Facility(name="상상이룸터", type="연습실", image_url="/static/img/room004.jpg", description="댄스 연습 특화 공간"),
                Facility(name="생각나눔터", type="회의실", image_url="/static/img/room005.jpg", description="회의 공간"),
            ]
            db.session.add_all(facilities)
            db.session.commit()
    app.run(debug=True, host="127.0.0.1", port=5000)