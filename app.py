from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from models import db, Facility, Reservation
from dotenv import load_dotenv
from datetime import datetime
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
    # DB에 있는 시설을 조회
    facilities = Facility.query.all()
    return render_template("index.html", SERVICE_NAME=SERVICE_NAME, facilities=facilities)

# 예약 페이지
@app.route('/reserve/<int:facility_id>', methods=["GET", "POST"])
def reserve(facility_id):
    selected_date = request.args.get("date")
    if not selected_date:
        flash("예약할 날짜를 선택해야 합니다.")
        return redirect(url_for("index"))

    selected_date_obj = datetime.strptime(selected_date, "%Y-%m-%d").date()
    today = datetime.today().date()
    if selected_date_obj <= today:
        flash("예약 날짜는 최소 하루 뒤 부터 가능합니다.")
        return redirect(url_for("index"))

    facility = Facility.query.get_or_404(facility_id)

    if request.method == "POST":
        name = request.form.get("name")
        contact = request.form.get("contact")
        school = request.form.get("school")
        club = request.form.get("club")
        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")
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
            flash("공지 및 준수사항에 동의해야 예약할 수 있습니다.")
            return redirect(url_for("reserve", facility_id=facility_id, date=selected_date))

        start_time = datetime.combine(selected_date_obj, datetime.strptime(start_time, "%H:%M").time())
        end_time = datetime.combine(selected_date_obj, datetime.strptime(end_time, "%H:%M").time())

        new_res = Reservation(
            facility_id=facility_id,
            applicant_name=name,
            applicant_contact=contact,
            applicant_school=school,
            applicant_club=club,
            start_time=start_time,
            end_time=end_time,
            participant_info=participants,
            requested_equipment=equipment,
            status="pending"
        )
        db.session.add(new_res)
        db.session.commit()

        return redirect(url_for("reservation_complete", res_id=new_res.id))

    return render_template("reserve.html", facility_id=facility_id, selected_date=selected_date, facility=facility)


# 예약 완료 페이지
@app.route('/complete/<int:res_id>')
def reservation_complete(res_id):
    res = Reservation.query.get_or_404(res_id)
    return render_template("complete.html", reservation=res, SERVICE_NAME=SERVICE_NAME)


if __name__ == "__main__":
    with app.app_context():
        db.create_all()
        # 시설 초기 데이터 추가
        if not Facility.query.first():
            facilities = [
                Facility(name="활력충전터", type="연습실"),
                Facility(name="창의키움터", type="연습실"),
                Facility(name="탐구개발터", type="회의실"),
                Facility(name="상상이룸터", type="회의실"),
                Facility(name="생각나눔터", type="회의실")
            ]
            db.session.add_all(facilities)
            db.session.commit()
    app.run(debug=True, host="127.0.0.1", port=5000)
