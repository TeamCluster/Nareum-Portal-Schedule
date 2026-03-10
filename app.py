from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from models import db, Facility, Reservation
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os, sys, io

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
                # [수정] is_deleted == False 조건 추가
                reservations = Reservation.query.filter(
                    Reservation.facility_id == facility.id,
                    Reservation.start_time >= start_of_day,
                    Reservation.start_time < end_of_day,
                    Reservation.is_deleted == False,
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

    # [수정] is_deleted == False 조건 추가
    existing_res = Reservation.query.filter(
        Reservation.facility_id == facility_id,
        Reservation.start_time >= datetime.combine(selected_date_obj, datetime.min.time()),
        Reservation.start_time < datetime.combine(selected_date_obj + timedelta(days=1), datetime.min.time()),
        Reservation.is_deleted == False,
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
        
        participants = {
            "elementary": int(request.form.get("elementary", 0)),
            "middle": int(request.form.get("middle", 0)),
            "high": int(request.form.get("high", 0)),
            "teen": int(request.form.get("teen", 0)),
            "adult": int(request.form.get("adult", 0))
        }
        equipment_list = request.form.getlist("equipment")
        
        start_hour = sorted([int(h) for h in selected_hours])[0]
        end_hour = sorted([int(h) for h in selected_hours])[-1] + 1
        start_dt = datetime.combine(selected_date_obj, datetime.strptime(f"{start_hour:02d}:00", "%H:%M").time())
        end_dt = datetime.combine(selected_date_obj, datetime.strptime(f"{end_hour:02d}:00", "%H:%M").time())

        # [수정] 중복 예약 검증 시 is_deleted == False 추가
        overlap = Reservation.query.filter(
            Reservation.facility_id == facility_id,
            Reservation.start_time < end_dt, 
            Reservation.end_time > start_dt,
            Reservation.is_deleted == False,
            Reservation.status.in_(["confirmed", "pending"])
        ).first()

        if overlap:
            flash("선택하신 시간에 이미 다른 예약이 진행 중입니다.")
            return redirect(url_for('reserve', facility_id=facility_id, date=selected_date_str))

        new_res = Reservation(
            facility_id=facility_id,
            applicant_name=name,
            applicant_contact=contact,
            applicant_school=school,
            applicant_club=club,
            start_time=start_dt,
            end_time=end_dt,
            participant_info=participants,       
            requested_equipment=equipment_list,  
            status="pending",
            is_deleted=False
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
    reservations = None
    search_name = search_contact = None
    if request.method == 'POST':
        search_name = request.form.get('name')
        search_contact = request.form.get('contact')
        
        # [수정] 거절 및 취소 내역도 확인해야 하므로 상태 필터(status.in_) 제거
        # 검색된 사용자의 모든 예약 내역을 불러와서 템플릿에서 분기 처리
        reservations = Reservation.query.filter(
            Reservation.applicant_name == search_name,
            Reservation.applicant_contact == search_contact
        ).order_by(Reservation.start_time.desc()).all()
        
    return render_template('check.html', reservations=reservations, search_name=search_name, search_contact=search_contact, SERVICE_NAME=SERVICE_NAME)

@app.route('/cancel/<int:res_id>', methods=['POST'])
def cancel(res_id):
    res = Reservation.query.get_or_404(res_id)
    # 삭제되지 않고 승인/대기 중인 예약만 취소 가능
    if not res.is_deleted and res.status in ['confirmed', 'pending']:
        res.status = 'cancelled'
        res.is_deleted = True # [추가] Soft Delete 처리
        db.session.commit()
        flash("예약이 정상적으로 취소되었습니다.")
    else:
        flash("이미 취소되었거나 유효하지 않은 예약입니다.")
        
    return redirect(url_for('check'))

# ==========================================
# 관리자 (Admin) 라우트
# ==========================================

@app.route('/admin')
def admin():
    # [수정] 대기 중이면서 삭제되지 않은 예약만 조회
    pending_reservations = Reservation.query.filter_by(status='pending', is_deleted=False).order_by(Reservation.start_time).all()
    return render_template('admin.html', pending_reservations=pending_reservations)

@app.route('/admin/api/events')
def admin_api_events():
    # [수정] 취소되거나 거절된 건(is_deleted=True)은 달력에서 제외
    reservations = Reservation.query.filter_by(is_deleted=False).all()
    events = []
    for res in reservations:
        color = '#ff9800' if res.status == 'pending' else '#4CAF50'
        events.append({
            'id': res.id,
            'title': f"[{res.facility.name}] {res.applicant_name}",
            'start': res.start_time.isoformat(),
            'end': res.end_time.isoformat(),
            'url': url_for('admin_edit', res_id=res.id),
            'color': color
        })
    return jsonify(events)

@app.route('/admin/api/booked_times')
def api_booked_times():
    facility_id = request.args.get('facility_id', type=int)
    date_str = request.args.get('date')
    
    if not facility_id or not date_str:
        return jsonify([])

    try:
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return jsonify([])

    start_of_day = datetime.combine(target_date, datetime.min.time())
    end_of_day = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

    # [수정] is_deleted == False 조건 추가
    reservations = Reservation.query.filter(
        Reservation.facility_id == facility_id,
        Reservation.start_time >= start_of_day,
        Reservation.start_time < end_of_day,
        Reservation.is_deleted == False,
        Reservation.status.in_(["confirmed", "pending"])
    ).all()

    booked = []
    for res in reservations:
        for h in range(res.start_time.hour, res.end_time.hour):
            booked.append(h)
            
    return jsonify(list(set(booked)))

@app.route('/admin/approve/<int:res_id>', methods=['POST'])
def admin_approve(res_id):
    res = Reservation.query.get_or_404(res_id)
    res.status = 'confirmed'
    db.session.commit()
    flash(f"{res.applicant_name}님의 예약이 승인되었습니다.")
    return redirect(request.referrer or url_for('admin'))

# [추가/수정] 기존 admin_delete 대신 거절(reject) 처리를 수행하는 라우트
@app.route('/admin/reject/<int:res_id>', methods=['POST'])
def admin_reject(res_id):
    res = Reservation.query.get_or_404(res_id)
    reason = request.form.get('reject_reason', '관리자 직권 거절')
    
    res.status = 'rejected'
    res.is_deleted = True # Soft delete 처리
    res.reject_reason = reason # 거절 사유 기록
    db.session.commit()
    
    flash(f"{res.applicant_name}님의 예약이 거절(취소) 처리되었습니다.")
    return redirect(request.referrer or url_for('admin'))

@app.route('/admin/edit/<int:res_id>', methods=['GET', 'POST'])
def admin_edit(res_id):
    res = Reservation.query.get_or_404(res_id)
    
    if request.method == 'POST':
        res.applicant_name = request.form.get('name')
        res.applicant_contact = request.form.get('contact')
        res.applicant_school = request.form.get('school')
        res.applicant_club = request.form.get('club')
        
        participants = {
            "elementary": int(request.form.get("elementary", 0)),
            "middle": int(request.form.get("middle", 0)),
            "high": int(request.form.get("high", 0)),
            "teen": int(request.form.get("teen", 0)),
            "adult": int(request.form.get("adult", 0))
        }
        res.participant_info = participants
        res.requested_equipment = request.form.getlist("equipment")
        
        # [수정] 상태 및 soft delete, 거절 사유 저장 로직 반영
        new_status = request.form.get('status')
        res.status = new_status
        
        if new_status in ['cancelled', 'rejected']:
            res.is_deleted = True
            if new_status == 'rejected':
                res.reject_reason = request.form.get('reject_reason', '')
        else:
            res.is_deleted = False
            res.reject_reason = None
            
        db.session.commit()
        flash("예약 정보가 성공적으로 수정되었습니다.")
        return redirect(url_for('admin_edit', res_id=res.id))
        
    participants = res.participant_info if res.participant_info else {}
    equipments_list = res.requested_equipment if res.requested_equipment else []

    return render_template('admin_edit.html', res=res, participants=participants, equipments_list=equipments_list)

@app.route('/admin/add', methods=['GET', 'POST'])
def admin_add():
    facilities = Facility.query.all()
    if request.method == 'POST':
        facility_id = request.form.get("facility_id")
        date_str = request.form.get("date")
        
        selected_hours = request.form.getlist("time_slot")
        if not selected_hours:
            flash("이용 시간을 하나 이상 선택해주세요.")
            return redirect(request.url)
            
        start_hour = sorted([int(h) for h in selected_hours])[0]
        end_hour = sorted([int(h) for h in selected_hours])[-1] + 1
        
        target_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        start_dt = datetime.combine(target_date, datetime.strptime(f"{start_hour:02d}:00", "%H:%M").time())
        end_dt = datetime.combine(target_date, datetime.strptime(f"{end_hour:02d}:00", "%H:%M").time())

        # [수정] is_deleted == False 추가
        overlap = Reservation.query.filter(
            Reservation.facility_id == facility_id,
            Reservation.start_time < end_dt, 
            Reservation.end_time > start_dt,
            Reservation.is_deleted == False,
            Reservation.status.in_(["confirmed", "pending"])
        ).first()

        if overlap:
            flash("선택하신 시설/시간에 이미 등록된 예약이 있어 추가할 수 없습니다.")
            return redirect(request.url)

        participants = {
            "elementary": int(request.form.get("elementary", 0)),
            "middle": int(request.form.get("middle", 0)),
            "high": int(request.form.get("high", 0)),
            "teen": int(request.form.get("teen", 0)),
            "adult": int(request.form.get("adult", 0))
        }
        if sum(participants.values()) == 0:
            flash("이용 인원은 최소 1명 이상이어야 합니다.")
            return redirect(request.url)
        equipment_list = request.form.getlist("equipment")

        new_res = Reservation(
            facility_id=facility_id,
            applicant_name=request.form.get("name"),
            applicant_contact=request.form.get("contact"),
            applicant_school=request.form.get("school"),
            applicant_club=request.form.get("club"),
            start_time=start_dt,
            end_time=end_dt,
            participant_info=participants, 
            requested_equipment=equipment_list, 
            status="confirmed",
            is_deleted=False
        )
        db.session.add(new_res)
        db.session.commit()
        flash("관리자 권한으로 예약을 성공적으로 추가했습니다.")
        return redirect(url_for('admin'))
        
    return render_template('admin_add.html', facilities=facilities)

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        if not Facility.query.first():
            facilities = [
                Facility(name="활력충전터", type="연습실", description="음악 연습 공간"),
                Facility(name="창의키움터", type="연습실", description="학습 공간"),
                Facility(name="탐구개발터", type="회의실", description="3D 프린터 및 회의"),
                Facility(name="상상이룸터", type="회의실"),
                Facility(name="생각나눔터", type="회의실")
            ]
            db.session.add_all(facilities)
            db.session.commit()
    app.run(debug=True, host="127.0.0.1", port=5000)