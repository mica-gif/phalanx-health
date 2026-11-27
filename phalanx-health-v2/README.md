# Phalanx Health

Copy of the Phalanx Health design PDF (logo, colors, light and dark mode) built as a working Flask app.

Clinic queue system for BatStateU Alangilan (CpE 411). Python + Flask + SQLite.

## Run on your computer
```
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000

## Demo accounts
| Role | ID | Password |
|---|---|---|
| Admin | ADMIN | admin123 |
| Doctor | DOC-001 | doctor123 |
| Nurse | NURSE-01 | nurse123 |
| Student | 24-09303 | student123 |

Students can also register themselves (SR-Code format 24-12345).
**Change these passwords before real use.**

## Deploy on Render
1. Create a GitHub repository and upload the **contents** of this folder so `app.py` and `requirements.txt` sit at the top level of the repo (do not upload the folder itself).
2. On render.com: New > Blueprint (or Web Service) > pick the repo.
3. Build command `pip install -r requirements.txt`, start command `gunicorn app:app`.
4. Deploy. Render gives you a public URL.

Note: on Render's free plan the disk resets on every deploy/restart, so the SQLite
data (students, tickets) is wiped. Fine for a class demo. For permanent data, add a
Render Persistent Disk and set env var `DB_PATH=/var/data/phalanx.db`, or move to Render Postgres.
