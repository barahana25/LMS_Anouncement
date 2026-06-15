import os
import sqlite3
import datetime
from canvasapi import Canvas
import telegram
import logging
import asyncio
import traceback
import glob
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
import subprocess
import shutil  # 추가
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

telegram_token = os.environ.get('TELEGRAM_TOKEN')
chat_id = os.environ.get('CHAT_ID')
if telegram_token is None or chat_id is None:
    logging.error("환경변수 'TELEGRAM_TOKEN' 또는 'CHAT_ID'가 설정되지 않았습니다.")
    raise ValueError("환경변수 'TELEGRAM_TOKEN' 또는 'CHAT_ID'가 설정되지 않았습니다.")

windows_path = r'C:\Users\barah\Desktop\Univ' # windows에서 실행 시
linux_path = '/Univ/Univ/2-1/' # linux에서 실행 시
# linux_path = '/discord/Univ/2-1/' # linux에서 실행 시
linux_parent_path = '/Univ/' # 로그 파일 저장 위치
path = windows_path if os.name == 'nt' else linux_path # 사용 운영체제에 따라 경로 설정
parent_path = linux_parent_path if os.name != 'nt' else windows_path
logging.basicConfig(filename=os.path.join(linux_parent_path, 'lms.log'), level=logging.INFO, format='%(asctime)s:%(levelname)s:%(message)s', encoding='utf-8')
logging.info("LMS Bot 시작")

db_path = os.path.join(linux_parent_path, "LMS.db")
API_URL = "https://canvas.kumoh.ac.kr"
API_KEY = os.environ.get('LMS_API_KEY')
KST = timezone(timedelta(hours=9))
API_REQUEST_TIMEOUT = (10, 30)  # connect timeout, read timeout
API_REQUEST_RETRIES = 3
API_RETRY_BACKOFF_SECONDS = 5

def format_to_kst(utc_str: str | None) -> str:
    if not utc_str:
        return "없음"
    try:
        # 1. 문자열을 datetime 객체로 변환 (Z는 +00:00으로 처리)
        utc_dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        # 2. KST(UTC+9)로 시간대 변경
        kst_dt = utc_dt.astimezone(KST)
        # 3. 원하는 포맷으로 반환
        return kst_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception as e:
        logging.error(f"날짜 변환 에러: {e}")
        return utc_str  # 실패 시 원본이라도 반환

if API_KEY is None:
    logging.error("환경변수 'LMS_API_KEY'가 설정되지 않았습니다.")
    raise ValueError("환경변수 'LMS_API_KEY'가 설정되지 않았습니다.")

bot = telegram.Bot(token=telegram_token)

async def send_telegram_message(message):
    await bot.send_message(chat_id=chat_id, text=message)

class TimeoutHTTPAdapter(HTTPAdapter):
    def __init__(self, *args, timeout=API_REQUEST_TIMEOUT, **kwargs):
        self.timeout = timeout
        super().__init__(*args, **kwargs)

    def send(self, request, **kwargs):
        kwargs.setdefault("timeout", self.timeout)
        return super().send(request, **kwargs)

def configure_canvas_session(session):
    retry = Retry(
        total=API_REQUEST_RETRIES,
        connect=API_REQUEST_RETRIES,
        read=API_REQUEST_RETRIES,
        status=API_REQUEST_RETRIES,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD", "OPTIONS"]),
        raise_on_status=False,
    )
    adapter = TimeoutHTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

async def get_planner_items(session, url, headers, params):
    last_error = None
    for attempt in range(1, API_REQUEST_RETRIES + 1):
        try:
            response = session.get(url, headers=headers, params=params, timeout=API_REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            last_error = e
            logging.warning(f"Canvas planner 요청 연결 실패 ({attempt}/{API_REQUEST_RETRIES}): {e}")
        except requests.exceptions.HTTPError as e:
            last_error = e
            status_code = e.response.status_code if e.response is not None else None
            if status_code is not None and status_code < 500 and status_code != 429:
                logging.error(f"Canvas planner 요청 실패(status={status_code}), 재시도하지 않음: {e}")
                return []
            logging.warning(f"Canvas planner 요청 HTTP 실패 ({attempt}/{API_REQUEST_RETRIES}): {e}")
        except requests.exceptions.RequestException as e:
            last_error = e
            logging.warning(f"Canvas planner 요청 실패 ({attempt}/{API_REQUEST_RETRIES}): {e}")
        except ValueError as e:
            logging.error(f"Canvas planner 응답 JSON 파싱 실패: {e}")
            return []

        if attempt < API_REQUEST_RETRIES:
            await asyncio.sleep(API_RETRY_BACKOFF_SECONDS * attempt)

    logging.error(f"Canvas planner 요청 재시도 실패, 이번 planner 알림은 건너뜁니다: {last_error}")
    return []

def parse_canvas_dt(s: str | None):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00"))  # aware(UTC)

def decide_d_day(now_kst: datetime, due_at_utc, has_submitted: bool):
    """
    반환: 3/1/0 중 하나 또는 None
    규칙:
      - unlock_at이 None 이거나 now < unlock → None (스킵)
      - has_submitted True → None (스킵)
      - now > due → None (스킵)
      - KST 날짜 기준으로 D-값 계산하여 {3,1,0}이면 그 값을 반환
    """
    if has_submitted:
        return None
    if due_at_utc is None:
        return None

    due_kst    = due_at_utc.astimezone(KST)

    if now_kst > due_kst:
        return None

    d_days = (due_kst.date() - now_kst.date()).days
    if d_days in (3, 1, 0):
        return d_days
    return None

def planner_submission_status(item):
    submissions = item.get("submissions")
    if submissions is False or submissions is None:
        return False
    if isinstance(submissions, dict):
        return bool(
            submissions.get("submitted")
            or submissions.get("submitted_at")
            or submissions.get("workflow_state") in {"submitted", "graded", "pending_review"}
        )
    return bool(submissions)

def to_str(dt):
    if dt is None: return None
    if isinstance(dt, str): return dt
    return dt.isoformat()

def values_differ(old_value, new_value) -> bool:
    return ("" if old_value is None else str(old_value)) != ("" if new_value is None else str(new_value))

def truncate_text(text, limit=700):
    if text is None:
        return "없음"
    text = str(text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."

def html_to_text(html):
    if not html:
        return "없음"
    return BeautifulSoup(html, 'html.parser').get_text("\n", strip=True) or "없음"

def build_change_message(title, course_name, item_name, changed_fields, field_labels, html_fields=None, date_fields=None):
    html_fields = html_fields or set()
    date_fields = date_fields or set()
    lines = [title, f"과목: {course_name}", f"항목: {item_name}", "변경 내용:"]
    for field, old_value, new_value in changed_fields:
        label = field_labels.get(field, field)
        if field in html_fields:
            old_text = truncate_text(html_to_text(old_value), 350)
            new_text = truncate_text(html_to_text(new_value), 350)
        elif field in date_fields:
            old_text = format_to_kst(old_value)
            new_text = format_to_kst(new_value)
        else:
            old_text = truncate_text(old_value, 350)
            new_text = truncate_text(new_value, 350)
        lines.append(f"- {label}: {old_text} → {new_text}")
    return truncate_text("\n".join(lines), 3900)

class DatabaseBase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.table_name = None

    def get_database(self):
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        try:
            cur.execute(f"SELECT * FROM {self.table_name} ORDER BY id")
        except sqlite3.OperationalError:
            con.close()
            return None
        temp = cur.fetchall()
        con.close()
        return temp

    def get_latest_data_id(self):
        all_db = self.get_database()
        if not all_db:
            return None
        return all_db[-1][0]

class AssignmentDB(DatabaseBase):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "assignment"

    def set_database(self, tr_list):
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS assignment (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        assignment_id INT,
                        course_id INT,
                        course_name TEXT,
                        assignment_name TEXT,
                        start_date TEXT NULL,
                        end_date TEXT NULL,
                        description TEXT NULL,
                        submitted INTEGER NOT NULL DEFAULT 0)""")
        columns = {row[1] for row in cur.execute("PRAGMA table_info(assignment)")}
        if "submitted" not in columns:
            cur.execute("ALTER TABLE assignment ADD COLUMN submitted INTEGER NOT NULL DEFAULT 0")
        changed_rows = []
        for assignment_id, course_id, course_name, assignment_name, start_date, end_date, description, submitted in tr_list:
            start_date = to_str(start_date)
            end_date = to_str(end_date)
            submitted_value = None if submitted is None else int(bool(submitted))
            new_values = {
                "course_id": course_id,
                "course_name": course_name,
                "assignment_name": assignment_name,
                "start_date": start_date,
                "end_date": end_date,
                "description": description,
            }
            cur.execute("""SELECT id, course_id, course_name, assignment_name, start_date, end_date, description, submitted
                           FROM assignment WHERE assignment_id=:Id""", {"Id": assignment_id})
            row = cur.fetchone()
            if row is None:
                submitted_value = submitted_value or 0
                cur.execute("""INSERT INTO assignment
                            (assignment_id, course_id, course_name, assignment_name, start_date, end_date, description, submitted)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (assignment_id, course_id, course_name, assignment_name, start_date, end_date, description, submitted_value))
                continue

            _, old_course_id, old_course_name, old_assignment_name, old_start_date, old_end_date, old_description, old_submitted = row
            if submitted_value is None:
                submitted_value = old_submitted
            new_values["submitted"] = submitted_value
            old_values = {
                "course_id": old_course_id,
                "course_name": old_course_name,
                "assignment_name": old_assignment_name,
                "start_date": old_start_date,
                "end_date": old_end_date,
                "description": old_description,
                "submitted": old_submitted,
            }
            changed_fields = [
                (field, old_values[field], new_values[field])
                for field in new_values
                if values_differ(old_values[field], new_values[field])
            ]
            if changed_fields:
                cur.execute("""UPDATE assignment
                               SET course_id=?, course_name=?, assignment_name=?, start_date=?, end_date=?, description=?, submitted=?
                               WHERE assignment_id=?""",
                            (course_id, course_name, assignment_name, start_date, end_date, description, submitted_value, assignment_id))
                changed_rows.append({
                    "assignment_id": assignment_id,
                    "course_name": course_name,
                    "assignment_name": assignment_name,
                    "changed_fields": changed_fields,
                })
        con.close()
        return changed_rows

class AnnouncementDB(DatabaseBase):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "announcement"

    def set_database(self, tr_list):
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS announcement (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        announcement_id INT,
                        course_id INT,
                        course_name TEXT,
                        announcement_title TEXT,
                        announcement_message TEXT,
                        posted_at TEXT NULL)""")
        changed_rows = []
        for announcement_id, course_id, course_name, announcement_title, announcement_message, posted_at in tr_list:
            posted_at = to_str(posted_at)
            new_values = {
                "course_id": course_id,
                "course_name": course_name,
                "announcement_title": announcement_title,
                "announcement_message": announcement_message,
                "posted_at": posted_at,
            }
            cur.execute("""SELECT id, course_id, course_name, announcement_title, announcement_message, posted_at
                           FROM announcement WHERE announcement_id=:Id""", {"Id": announcement_id})
            row = cur.fetchone()
            if row is None:
                cur.execute("""INSERT INTO announcement 
                            (announcement_id, course_id, course_name, announcement_title, announcement_message, posted_at) 
                            VALUES (?, ?, ?, ?, ?, ?)""",
                            (announcement_id, course_id, course_name, announcement_title, announcement_message, posted_at))
                continue

            _, old_course_id, old_course_name, old_title, old_message, old_posted_at = row
            old_values = {
                "course_id": old_course_id,
                "course_name": old_course_name,
                "announcement_title": old_title,
                "announcement_message": old_message,
                "posted_at": old_posted_at,
            }
            changed_fields = [
                (field, old_values[field], new_values[field])
                for field in new_values
                if values_differ(old_values[field], new_values[field])
            ]
            if changed_fields:
                cur.execute("""UPDATE announcement
                               SET course_id=?, course_name=?, announcement_title=?, announcement_message=?, posted_at=?
                               WHERE announcement_id=?""",
                            (course_id, course_name, announcement_title, announcement_message, posted_at, announcement_id))
                changed_rows.append({
                    "announcement_id": announcement_id,
                    "course_name": course_name,
                    "announcement_title": announcement_title,
                    "changed_fields": changed_fields,
                })
        con.close()
        return changed_rows

class CourseDB(DatabaseBase):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "course"

    def set_database(self, tr_list):
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS course (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_id INT,
                        course_name TEXT,
                        course_code TEXT)""")
        changed_rows = []
        for course_id, course_name, course_code in tr_list:
            new_values = {"course_name": course_name, "course_code": course_code}
            cur.execute("SELECT id, course_name, course_code FROM course WHERE course_id=:Id", {"Id": course_id})
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO course (course_id, course_name, course_code) VALUES (?, ?, ?)",
                            (course_id, course_name, course_code))
                continue

            _, old_course_name, old_course_code = row
            old_values = {"course_name": old_course_name, "course_code": old_course_code}
            changed_fields = [
                (field, old_values[field], new_values[field])
                for field in new_values
                if values_differ(old_values[field], new_values[field])
            ]
            if changed_fields:
                cur.execute("UPDATE course SET course_name=?, course_code=? WHERE course_id=?",
                            (course_name, course_code, course_id))
                changed_rows.append({
                    "course_id": course_id,
                    "course_name": course_name,
                    "changed_fields": changed_fields,
                })
        con.close()
        return changed_rows

class LectureDB(DatabaseBase):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "lecture"

    def set_database(self, tr_list):
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS lecture (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_id INT,
                        course_name TEXT,
                        file_name TEXT,
                        file_size INT)""")
        changed_rows = []
        for course_id, course_name, file_name, file_size in tr_list:
            file_name = file_name.replace("'", "''")
            new_values = {"course_name": course_name, "file_size": file_size}
            cur.execute("SELECT id, course_name, file_size FROM lecture WHERE course_id=:Id AND file_name=:File",
                        {"Id": course_id, "File": file_name})
            row = cur.fetchone()
            if row is None:
                cur.execute("""INSERT INTO lecture 
                            (course_id, course_name, file_name, file_size) 
                            VALUES (?, ?, ?, ?)""",
                            (course_id, course_name, file_name, file_size))
                continue

            _, old_course_name, old_file_size = row
            old_values = {"course_name": old_course_name, "file_size": old_file_size}
            changed_fields = [
                (field, old_values[field], new_values[field])
                for field in new_values
                if values_differ(old_values[field], new_values[field])
            ]
            if changed_fields:
                cur.execute("UPDATE lecture SET course_name=?, file_size=? WHERE course_id=? AND file_name=?",
                            (course_name, file_size, course_id, file_name))
                changed_rows.append({
                    "course_id": course_id,
                    "course_name": course_name,
                    "file_name": file_name,
                    "changed_fields": changed_fields,
                })
        con.close()
        return changed_rows

class NotificationDB(DatabaseBase):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "assignment_notify"
        self._ensure_table()

    def _ensure_table(self):
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS assignment_notify (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                assignment_id INTEGER NOT NULL,
                d_day INTEGER NOT NULL,          -- 3, 1, 0
                sent_at TEXT NOT NULL
            )
        """)
        # 중복 방지: 같은 과제의 동일 D-day는 한 번만 기록
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_assignment_notify_unique
            ON assignment_notify (assignment_id, d_day)
        """)
        con.close()

    def was_sent(self, assignment_id: int, d_day: int) -> bool:
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute(
            "SELECT 1 FROM assignment_notify WHERE assignment_id=? AND d_day=?",
            (assignment_id, d_day)
        )
        ok = cur.fetchone() is not None
        con.close()
        return ok

    def mark_sent(self, assignment_id: int, d_day: int, sent_at: str) -> None:
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute(
            "INSERT OR IGNORE INTO assignment_notify (assignment_id, d_day, sent_at) VALUES (?, ?, ?)",
            (assignment_id, d_day, sent_at)
        )
        con.close()

class DatabaseWatcher:
    def __init__(self, db_instance):
        self.db = db_instance
        self.last_seen_id = self.db.get_latest_data_id() or 0

    def check_for_update(self):
        con = sqlite3.connect(self.db.db_path, isolation_level=None)
        cur = con.cursor()
        try:
            cur.execute(f"SELECT * FROM {self.db.table_name} WHERE id > ?", (self.last_seen_id,))
            new_data = cur.fetchall()
        except sqlite3.OperationalError:
            con.close()
            return []
        con.close()
        if new_data:
            self.last_seen_id = max(row[0] for row in new_data)
        return new_data

def make_dir(dir_name):
    if not os.path.exists(dir_name):
        os.makedirs(dir_name)

def cleanup_pdf_images(*bases):
    for base in bases:
        for image_path in glob.glob(f"{base}-*.png"):
            os.remove(image_path)

def render_pdf_pages(pdf_path, output_base):
    result = subprocess.run(
        ["pdftoppm", "-png", pdf_path, output_base],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return None
    return (result.stderr or result.stdout or f"exit status {result.returncode}").strip()

async def main(canvas, course_db, assignment_db, announcement_db, lecture_db, notification_db):
    make_dir(os.path.join(linux_parent_path, "tmp"))
    session = canvas._Canvas__requester._session  # 내부 세션 객체
    configure_canvas_session(session)
    courses = canvas.get_courses(enrollment_state='active')
    now_kst = datetime.now(timezone.utc).astimezone(KST)
    
    # 2️⃣ canvasapi의 세션 재사용
    headers = {"Authorization": f"Bearer {API_KEY}"}

    # 3️⃣ planner/items 엔드포인트 호출
    url = f"{API_URL}/api/v1/planner/items"
    params = {"start_date": now_kst.strftime("%Y-%m-%dT%H:%M:%S.000Z")}

    data = await get_planner_items(session, url, headers, params)
    planner_submissions = {}

    for item in data:
        html_url = item.get("html_url") or ""
        try:
            assignment_id = int(html_url.rstrip("/").split("/")[-1])
        except (TypeError, ValueError):
            logging.warning(f"planner 과제 ID 확인 실패: {html_url}")
            continue
        course_name = item.get("context_name").split('-')[0]
        due_at_utc = parse_canvas_dt(item.get("plannable").get("due_at"))
        has_submitted = planner_submission_status(item)
        planner_submissions[assignment_id] = has_submitted
        assignment_name = item.get("plannable").get("title")

        d_day = decide_d_day(now_kst, due_at_utc, has_submitted)
        logging.info(f"d-day 확인, course_name: {course_name}, assignment_id: {assignment_id}, assignment_name: {assignment_name}, now_kst: {now_kst}, due_at_utc: {due_at_utc}, has_submitted: {has_submitted} → d_day: {d_day}")
        if d_day is not None:
            if not notification_db.was_sent(assignment_id, d_day):
                due_kst = due_at_utc.astimezone(KST)
                if d_day == 0:
                    d_day = "day"
                msg = (
                    f"[과제 마감 알림] D-{d_day}\n"
                    f"과목: {course_name}\n"
                    f"과제: {assignment_name}\n"
                    f"마감: {due_kst.strftime('%Y-%m-%d %H:%M:%S')} (KST)"
                )
                await send_telegram_message(msg)
                if d_day == "day":
                    d_day = 0
                notification_db.mark_sent(
                    assignment_id=assignment_id,
                    d_day=d_day,
                    sent_at=now_kst.strftime("%Y-%m-%d %H:%M:%S")
                )
    course_list, assignment_list, lecture_list, announcement_list = [], [], [], []

    for course in courses:
        course_name = course.name.split('-')[0]
        course_code = '-'.join(course.course_code.split('-')[1:])
        course_list.append((course.id, course_name, course_code))

        # 공지 처리
        for announcement in course.get_discussion_topics(only_announcements=True):
            announcement_list.append((
                announcement.id,
                course.id,
                course_name,
                announcement.title,
                announcement.message,
                announcement.posted_at
            ))

        # 과제 처리 (+ D-day 알림)
        for assignment in course.get_assignments():
            unlock_at = assignment.unlock_at
            if unlock_at is None:
                unlock_at = assignment.created_at
            due_at = assignment.due_at
            if due_at is None:
                due_at = assignment.lock_at
            submitted = planner_submissions.get(assignment.id)
            if submitted is None:
                try:
                    submission = assignment.get_submission("self")
                    submitted = bool(
                        getattr(submission, "submitted_at", None)
                        or getattr(submission, "workflow_state", None) in {"submitted", "graded", "pending_review"}
                    )
                except Exception as e:
                    submitted = None
                    logging.warning(f"과제 제출 여부 확인 실패 ({assignment.id}): {e}")
            assignment_list.append((
                assignment.id,
                course.id,
                course_name,
                assignment.name,
                unlock_at,
                due_at,
                assignment.description,
                submitted,
            ))
            logging.info(f"과제 처리, course_name: {course_name}, assignment_id: {assignment.id}, assignment_name: {assignment.name}, unlock_at: {unlock_at}, due_at: {due_at}")

        files = list(course.get_files())
        if files:
            make_dir(os.path.join(path, course_name))
        for file in files:
            if file.locked_for_user == True:
                continue
            lecture_list.append((course.id, course_name, file.display_name, file.size))

            sub_dir = '강의자료' if any(ext in file.display_name.lower() for ext in ['pdf', 'ppt', 'doc', 'hwp']) else '기타파일'
            make_dir(os.path.join(path, course_name, sub_dir))

            save_path = os.path.join(path, course_name, sub_dir, file.display_name)
            if os.path.exists(save_path) and file.size is not None:
                if os.path.getsize(save_path) == file.size:
                    logging.info(f"✅ 이미 존재하는 파일이며 크기 동일: {file.display_name}, 다운로드 생략")
                    continue
                else:
                    if file.display_name.lower().endswith('.pdf'):
                        old_base = os.path.join(linux_parent_path, "tmp/old_file")
                        new_base = os.path.join(linux_parent_path, "tmp/new_file")
                        diff_base = os.path.join(linux_parent_path, "tmp/diff")

                        tmp_new_pdf = os.path.join(linux_parent_path, "tmp/new_file.pdf")
                        cleanup_pdf_images(old_base, new_base, diff_base)
                        file.download(tmp_new_pdf)

                        old_pdf_error = render_pdf_pages(save_path, old_base)
                        new_pdf_error = render_pdf_pages(tmp_new_pdf, new_base)
                        if old_pdf_error or new_pdf_error:
                            errors = []
                            if old_pdf_error:
                                errors.append(f"기존 PDF: {old_pdf_error}")
                            if new_pdf_error:
                                errors.append(f"새 PDF: {new_pdf_error}")
                            error_detail = " / ".join(errors)
                            logging.warning(
                                f"PDF 페이지 비교 생략 ({course_name} / {file.display_name}): {error_detail}"
                            )
                            shutil.move(tmp_new_pdf, save_path)
                            cleanup_pdf_images(old_base, new_base, diff_base)
                            await send_telegram_message(
                                f"{course_name} 강의 {file.display_name} 파일이 변경되었습니다. "
                                f"PDF 암호화 또는 손상으로 페이지 비교는 생략했습니다."
                            )
                            continue

                        # 🔹 4️⃣ 모든 페이지 비교
                        old_pages = sorted(glob.glob(f"{old_base}-*.png"))
                        changed_pages = []

                        for old_img in old_pages:
                            # old_file-1.png → 1 추출
                            page_num = os.path.basename(old_img).split('-')[-1].split('.')[0]
                            new_img = f"{new_base}-{page_num}.png"
                            diff_img = f"{diff_base}-{page_num}.png"

                            if not os.path.exists(new_img):
                                continue  # 새 파일에 해당 페이지 없음 → skip

                            diff_result = subprocess.run(
                                f"diff -q '{old_img}' '{new_img}' > /dev/null",
                                shell=True
                            )

                            # 다를 때만 diff 이미지 생성
                            if diff_result.returncode != 0:
                                subprocess.run(
                                    f"compare '{old_img}' '{new_img}' '{diff_img}'",
                                    shell=True
                                )
                                changed_pages.append(page_num)
                        # 🔹 5️⃣ 결과 처리
                        if changed_pages:
                            page_str = ", ".join(changed_pages)
                            logging.info(f"⚠️ {file.display_name} 변경 감지 (페이지: {page_str})")
                            await send_telegram_message(
                                f"📄 {course_name} 강의 '{file.display_name}' 변경 감지됨 (페이지: {page_str})"
                            )
                        else:
                            logging.info(f"✅ {file.display_name} 내용 동일 (크기만 다름)")

                        # 🔹 6️⃣ 새 파일로 교체
                        # os.replace(tmp_new_pdf, save_path)
                        shutil.move(tmp_new_pdf, save_path)

                        # 🔹 7️⃣ 임시 PNG 정리
                        cleanup_pdf_images(old_base, new_base, diff_base)
                        await send_telegram_message(f"{course_name} 강의 {file.display_name} {', '.join(changed_pages)} 페이지 변경됨")
                        continue
                        
                    else:
                        logging.info(f"🔄 파일 크기 다름, 다시 다운로드: {file.display_name}")
                        await send_telegram_message(f"{course_name} 강의 {file.display_name} 파일 크기가 다름")
            else:
                logging.info(f"⬇️ 새 파일 다운로드: {file.display_name}")
                await send_telegram_message(f"{course_name} 강의 {file.display_name} 파일 다운로드")

            file.download(save_path)

        await asyncio.sleep(1)

    changed_data = {
        "courses": course_db.set_database(course_list),
        "assignments": assignment_db.set_database(assignment_list),
        "announcements": announcement_db.set_database(announcement_list),
        "lectures": lecture_db.set_database(lecture_list),
    }
    return changed_data

async def loop_main():
    course_db = CourseDB(db_path)
    assignment_db = AssignmentDB(db_path)
    announcement_db = AnnouncementDB(db_path)
    lecture_db = LectureDB(db_path)
    notification_db = NotificationDB(db_path)

    assignment_watcher = DatabaseWatcher(assignment_db)
    announcement_watcher = DatabaseWatcher(announcement_db)
    lecture_watcher = DatabaseWatcher(lecture_db)

    while True:
        now = datetime.now()
        current_hour = now.hour

        if 2 <= current_hour < 6:
            logging.info(f"🛌 현재 {current_hour}시: 휴식 시간입니다. 1시간 후 다시 확인합니다.")
            await asyncio.sleep(3600)
            continue

        try:
            logging.info(f"작업 시작 ({now.strftime('%Y-%m-%d %H:%M:%S')})")
            canvas = Canvas(API_URL, API_KEY)
            changed_data = await main(canvas, course_db, assignment_db, announcement_db, lecture_db, notification_db)
            logging.info(f"작업 완료 ({now.strftime('%Y-%m-%d %H:%M:%S')})")

            for changed in changed_data["announcements"]:
                logging.info(f"공지 변경 감지: {changed['announcement_id']}, {changed['announcement_title']}")
                message = build_change_message(
                    "[공지 변경 알림]",
                    changed["course_name"],
                    changed["announcement_title"],
                    changed["changed_fields"],
                    {
                        "course_id": "과목 ID",
                        "course_name": "과목명",
                        "announcement_title": "공지 제목",
                        "announcement_message": "게시글",
                        "posted_at": "게시일",
                    },
                    html_fields={"announcement_message"},
                    date_fields={"posted_at"},
                )
                await send_telegram_message(message)

            for changed in changed_data["assignments"]:
                logging.info(f"과제 변경 감지: {changed['assignment_id']}, {changed['assignment_name']}")
                message = build_change_message(
                    "[과제 변경 알림]",
                    changed["course_name"],
                    changed["assignment_name"],
                    changed["changed_fields"],
                    {
                        "course_id": "과목 ID",
                        "course_name": "과목명",
                        "assignment_name": "과제명",
                        "start_date": "시작일",
                        "end_date": "마감일",
                        "description": "내용",
                    },
                    html_fields={"description"},
                    date_fields={"start_date", "end_date"},
                )
                await send_telegram_message(message)

            for changed in changed_data["courses"]:
                logging.info(f"강의 변경 감지: {changed['course_id']}, {changed['course_name']}")
                message = build_change_message(
                    "[과목 정보 변경 알림]",
                    changed["course_name"],
                    changed["course_name"],
                    changed["changed_fields"],
                    {
                        "course_name": "과목명",
                        "course_code": "과목 코드",
                    },
                )
                await send_telegram_message(message)

            for changed in changed_data["lectures"]:
                logging.info(f"강의자료 DB 변경 감지: {changed['course_name']}, {changed['file_name']}")
                message = build_change_message(
                    "[강의자료 변경 알림]",
                    changed["course_name"],
                    changed["file_name"],
                    changed["changed_fields"],
                    {
                        "course_name": "과목명",
                        "file_size": "파일 크기",
                    },
                )
                await send_telegram_message(message)

            new_announcements = announcement_watcher.check_for_update()
            for row in new_announcements:
                logging.info(f"과목명: {row[3]}, 공지명: {row[4]}")
                posted_at = format_to_kst(row[6])
                soup_description = BeautifulSoup(row[5], 'html.parser')
                # <p> 태그 기준으로 텍스트 추출
                paragraphs = [p.get_text(strip=True) for p in soup_description.find_all('p') if p.get_text(strip=True)]

                # '\n'으로 구분된 문자열로 출력
                result = "\n".join(paragraphs)
                await send_telegram_message(f"{row[3]} 과목에 새로운 공지 {row[4]}이 등록됨\n게시글: {result}\n게시일: {posted_at}")
            new_assignments = assignment_watcher.check_for_update()
            for row in new_assignments:
                logging.info(f"과제 ID: {row[2]}, 과목명: {row[3]}, 과제명: {row[4]}")
                start_time = format_to_kst(row[5])
                end_time = format_to_kst(row[6])
                description = row[7] if row[7] else "없음"
                soup_description = BeautifulSoup(description, 'html.parser')
                description_text = soup_description.get_text().strip()
                # description_text = description_text.replace("&nbsp;", "\n")
                await send_telegram_message(f"{row[3]} 과목에 새로운 과제 {row[4]}이 등록됨\n시작일: {start_time}\n마감일: {end_time}\n내용:\n{description_text}")

            new_lectures = lecture_watcher.check_for_update()
            for row in new_lectures:
                logging.info(f"과목명: {row[2]}, 파일명: {row[3]}")
                # await send_telegram_message(f"{row[2]} 과목에 새로운 강의자료 {row[3]}이 등록됨")

        except Exception as e:
            logging.error(f"에러 발생: {traceback.format_exc()}")
            await send_telegram_message(f"❗ LMS Bot 에러 발생: {e}")

        await asyncio.sleep(600)

if __name__ == "__main__":
    asyncio.run(loop_main())
