from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from models import db, Facility, Reservation
from dotenv import load_dotenv
from datetime import datetime, timedelta
import os, sys, io, json

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ["PYTHONIOENCODING"] = "utf-8"
load_dotenv()

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///resv.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.secret_key = "secret"   # flash 메시지용

SERVICE_NAME = "나름센터 활동실 대관"
db.init_app(app)

# 메인 페이지
@app.route('/')
def index():
    facilities = [
        {"id": 1, "name": "A"},
        {"id": 2, "name": "B"},
        {"id": 3, "name": "C"},
        {"id": 4, "name": "D"}
    ]
    return render_template("index.html", SERVICE_NAME=SERVICE_NAME, facilities=facilities)

# 예약 신청 페이지 (GET: 폼, POST: 저장)
@app.route('/reserve/<int:facility_id>', methods=["GET", "POST"])
def reserve(facility_id):
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
            return redirect(url_for("reserve", facility_id=facility_id))

        # DB 저장
        new_res = Reservation(
            facility_id=facility_id,
            applicant_name=name,
            applicant_contact=contact,
            applicant_school=school,
            applicant_club=club,
            start_time=datetime.strptime(start_time, "%H:%M"),
            end_time=datetime.strptime(end_time, "%H:%M"),
            participant_info=participants,
            requested_equipment=equipment,
            status="pending"
        )
        db.session.add(new_res)
        db.session.commit()
        flash("예약이 완료되었습니다!")
        return redirect(url_for("index"))

    # GET 요청 시 예약 페이지 보여주기
    return render_template("reserve.html", facility_id=facility_id)

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)
