from flask import Flask, render_template
from flask_sqlalchemy import SQLAlchemy
from models import db
from dotenv import load_dotenv
from datetime import datetime
from io import StringIO
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
os.environ["PYTHONIOENCODING"] = "utf-8"
load_dotenv()

# Flask App & Config 정의
app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///resv.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

SERVICE_NAME = "나름센터 활동실 대관"

# DB Setup
db.init_app(app)

# ====== 라우팅 ======

# 맨 처음 페이지
@app.route('/')
def index():
    return render_template("index.html", SERVICE_NAME=SERVICE_NAME)

# ===================


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)