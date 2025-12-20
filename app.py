from flask import Flask, render_template, request, redirect, url_for, flash
from models import db, Facility, Reservation
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os, sys, io

# 한글 출력 설정
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
    is_reservable = True  # 예약 가능 여부 (날짜 기준)

    if selected_date_str:
        try:
            target_date = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
            today = datetime.today().date()

            # [수정됨] 조회 시점에 날짜 검사: 오늘 포함 이전 날짜는 경고 메시지 출력
            if target_date <= today:
                flash("예약 신청은 내일 날짜부터 가능합니다. (현재는 현황 조회만 가능합니다)")
                is_reservable = False

            start_of_day = datetime.combine(target_date, datetime.min.time())
            end_of_day = datetime.combine(target_date + timedelta(days=1), datetime.min.time())

            for facility in facilities:
                reservations = Reservation.query.filter(
                    Reservation.facility_id == facility.id,
                    Reservation.start_time >= start_of_day,
                    Reservation.start_time < end_of_day
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
        is_reservable=is_reservable # 템플릿으로 전달
    )

@app.route('/reserve/<int:facility_id>', methods=["GET", "POST"])
def reserve(facility_id):
    selected_date_str = request.args.get("date")
    if not selected_date_str:
        flash("예약할 날짜를 먼저 선택해주세요.")
        return redirect(url_for("index"))

    try:
        selected_date_obj = datetime.strptime(selected_date_str, "%Y-%m-%d").date()
    except ValueError:
        flash("날짜 형식이 올바르지 않습니다.")
        return redirect(url_for("index"))

    today = datetime.today().date()
    if selected_date_obj <= today:
        flash("예약 날짜는 내일부터 가능합니다.")
        return redirect(url_for("index"))

    facility = Facility.query.get_or_404(facility_id)

    existing_res = Reservation.query.filter(
        Reservation.facility_id == facility_id,
        Reservation.start_time >= datetime.combine(selected_date_obj, datetime.min.time()),
        Reservation.start_time < datetime.combine(selected_date_obj + timedelta(days=1), datetime.min.time())
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
        
        if not selected_hours:
            flash("이용할 시간을 최소 1개 이상 선택해야 합니다.")
            return redirect(url_for("reserve", facility_id=facility_id, date=selected_date_str))

        selected_hours = sorted([int(h) for h in selected_hours])
        for i in range(len(selected_hours) - 1):
            if selected_hours[i] + 1 != selected_hours[i+1]:
                flash("연속된 시간만 예약 가능합니다.")
                return redirect(url_for("reserve", facility_id=facility_id, date=selected_date_str))

        start_hour = selected_hours[0]
        end_hour = selected_hours[-1] + 1 

        start_dt = datetime.combine(selected_date_obj, datetime.strptime(f"{start_hour:02d}:00", "%H:%M").time())
        end_dt = datetime.combine(selected_date_obj, datetime.strptime(f"{end_hour:02d}:00", "%H:%M").time())

        overlap = Reservation.query.filter(
            Reservation.facility_id == facility_id,
            Reservation.start_time < end_dt, 
            Reservation.end_time > start_dt
        ).first()

        if overlap:
            flash("선택하신 시간에 이미 다른 예약이 존재합니다. 다시 확인해주세요.")
            return redirect(url_for("reserve", facility_id=facility_id, date=selected_date_str))

        participants = {
            "elementary": request.form.get("elementary", 0),
            "middle": request.form.get("middle", 0),
            "high": request.form.get("high", 0),
            "teen": request.form.get("teen", 0),
            "adult": request.form.get("adult", 0),
        }
        equipment = request.form.getlist("equipment")
        agree = request.form.get("agree")

        if not agree:
            flash("공지 및 준수사항에 동의해야 합니다.")
            return redirect(url_for("reserve", facility_id=facility_id, date=selected_date_str))

        new_res = Reservation(
            facility_id=facility_id,
            applicant_name=name,
            applicant_contact=contact,
            applicant_school=school,
            applicant_club=club,
            start_time=start_dt,
            end_time=end_dt,
            participant_info=participants,
            requested_equipment=equipment,
            status="confirmed" 
        )
        db.session.add(new_res)
        db.session.commit()

        return redirect(url_for("reservation_complete", res_id=new_res.id))

    return render_template(
        "reserve.html", 
        facility_id=facility_id, 
        selected_date=selected_date_str, 
        facility=facility,
        booked_hours=booked_hours 
    )

@app.route('/complete/<int:res_id>')
def reservation_complete(res_id):
    res = Reservation.query.get_or_404(res_id)
    return render_template("complete.html", reservation=res, SERVICE_NAME=SERVICE_NAME)

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