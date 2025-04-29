# ------------------------------- #
# 1. 라이브러리 임포트
# ------------------------------- #
import os
import sqlite3
import datetime
from canvasapi import Canvas
import telegram
import logging
import asyncio
import traceback

# ------------------------------- ## 0. 환경변수 설정# ------------------------------- #
telegram_token = os.environ.get('TELEGRAM_TOKEN') # 텔레그램 봇 토큰
chat_id = os.environ.get('CHAT_ID') # 텔레그램 채팅방 ID
if telegram_token is None or chat_id is None:
    logging.error(
        f"환경변수 'TELEGRAM_TOKEN' 또는 'CHAT_ID'가 설정되지 않았습니다."
    )
    raise ValueError("환경변수 'TELEGRAM_TOKEN' 또는 'CHAT_ID'가 설정되지 않았습니다.")

windows_path = r'C:\Users\barah\Desktop\Univ'
linux_path = '/Univ/Univ'
linux_parent_path = '/Univ/'
path = windows_path if os.name == 'nt' else linux_path
parent_path = linux_parent_path if os.name != 'nt' else windows_path
logging.basicConfig(filename=os.path.join(linux_parent_path, 'lms.log'), level=logging.INFO, format='%(asctime)s:%(levelname)s:%(message)s', encoding='utf-8')
logging.info("LMS Bot 시작")

db_path = os.path.join(linux_parent_path, "LMS.db")

API_URL = "https://canvas.kumoh.ac.kr"
API_KEY = os.environ.get('LMS_API_KEY')
if API_KEY is None:
    logging.error(   
        "환경변수 'LMS_API_KEY'가 설정되지 않았습니다."
    )
    raise ValueError("환경변수 'LMS_API_KEY'가 설정되지 않았습니다.")

# ---------------------------------------------------------------------------------- #

bot = telegram.Bot(token=telegram_token)

async def send_telegram_message(message):
    await bot.send_message(chat_id=chat_id, text=message)
# ------------------------------- #
# 2. 공통 부모 클래스
# ------------------------------- #
class DatabaseBase:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self.table_name = None

    def get_database(self) -> list | None:
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

    def get_database_from_id(self, id: int) -> tuple | None:
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        try:
            cur.execute(f"SELECT * FROM {self.table_name} WHERE id=:Id", {"Id": id})
        except sqlite3.OperationalError:
            con.close()
            return None
        temp = cur.fetchone()
        con.close()
        return temp

    def get_latest_data_id(self) -> int | None:
        all_db = self.get_database()
        if not all_db:
            return None
        return all_db[-1][0]

# ------------------------------- #
# 3. 각 테이블 DB 클래스
# ------------------------------- #
class CourseDB(DatabaseBase):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "course"

    def set_database(self, tr_list: list) -> None:
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS course (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_id INT,
                        course_name TEXT,
                        course_code TEXT
                    )""")
        for course_id, course_name, course_code in tr_list:
            cur.execute("SELECT * FROM course WHERE course_id=:Id", {"Id": course_id})
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO course (course_id, course_name, course_code) VALUES (?, ?, ?)",
                    (course_id, course_name, course_code)
                )
        con.close()

class AssignmentDB(DatabaseBase):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "assignment"

    def set_database(self, tr_list: list) -> None:
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS assignment (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        assignment_id INT,
                        course_id INT,
                        course_name TEXT,
                        start_date TEXT NULL,
                        end_date TEXT NULL,
                        description TEXT NULL
                    )""")
        for assignment_id, course_id, course_name, start_date, end_date, description in tr_list:
            cur.execute("SELECT * FROM assignment WHERE assignment_id=:Id", {"Id": assignment_id})
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO assignment (assignment_id, course_id, course_name, start_date, end_date, description) VALUES (?, ?, ?, ?, ?, ?)",
                    (assignment_id, course_id, course_name, start_date, end_date, description)
                )
        con.close()

class LectureDB(DatabaseBase):
    def __init__(self, db_path: str):
        super().__init__(db_path)
        self.table_name = "lecture"

    def set_database(self, tr_list: list) -> None:
        con = sqlite3.connect(self.db_path, isolation_level=None)
        cur = con.cursor()
        cur.execute("""CREATE TABLE IF NOT EXISTS lecture (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_id INT,
                        course_name TEXT,
                        file_name TEXT,
                        file_size INT
                    )""")
        for course_id, course_name, file_name, file_size in tr_list:
            file_name = file_name.replace("'", "''")
            cur.execute("SELECT * FROM lecture WHERE course_id=:Id AND file_name=:File", {"Id": course_id, "File": file_name})
            if cur.fetchone() is None:
                cur.execute(
                    "INSERT INTO lecture (course_id, course_name, file_name, file_size) VALUES (?, ?, ?, ?)",
                    (course_id, course_name, file_name, file_size)
                )
        con.close()

# ------------------------------- #
# 4. 데이터 변경 감지 감시자
# ------------------------------- #
class DatabaseWatcher:
    def __init__(self, db_instance):
        self.db = db_instance
        self.last_seen_id = self.db.get_latest_data_id() or 0

    def check_for_update(self) -> list:
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

# ------------------------------- #
# 5. 보조 함수 (폴더 만들기)
# ------------------------------- #
def make_dir(dir_name):
    if not os.path.exists(dir_name):
        os.makedirs(dir_name)

# ------------------------------- #
# 6. 메인 로직
# ------------------------------- #
async def main():
    canvas = Canvas(API_URL, API_KEY)

    # DB 인스턴스 준비
    course_db = CourseDB(db_path)
    assignment_db = AssignmentDB(db_path)
    lecture_db = LectureDB(db_path)

    # course, assignment, lecture 데이터 수집
    courses = canvas.get_courses()

    course_list = []
    assignment_list = []
    lecture_list = []

    for course in courses:
        course_name = course.name.split('-')[0]
        course_code = '-'.join(course.course_code.split('-')[1:])
        course_list.append((course.id, course_name, course_code))

        assignments = course.get_assignments()
        for assignment in assignments:
            assignment_list.append((
                assignment.id,
                course.id,
                course_name,
                assignment.unlock_at,
                assignment.due_at,
                assignment.description
            ))

        course_file_list = course.get_files()
        for file in course_file_list:
            lecture_list.append((
                course.id,
                course_name,
                file.display_name,
                file.size
            ))

            # 파일 다운로드
            if list(course_file_list):
                make_dir(os.path.join(path, course_name))
            else:
                continue

            if any(ext in file.display_name.lower() for ext in ['pdf', 'ppt', 'doc', 'hwp']):
                make_dir(os.path.join(path, course_name, '강의자료'))
            else:
                make_dir(os.path.join(path, course_name, '기타파일'))

            if any(ext in file.display_name.lower() for ext in ['pdf', 'ppt', 'doc', 'hwp']):
                save_path = os.path.join(path, course_name, '강의자료', file.display_name)
            else:
                save_path = os.path.join(path, course_name, '기타파일', file.display_name)

            # 파일이 존재하면 크기 비교
            if os.path.exists(save_path):
                local_size = os.path.getsize(save_path)
                if file.size is not None and local_size == file.size:
                    logging.info(f"✅ 이미 존재하는 파일이며 크기 동일: {file.display_name}, 다운로드 생략")
                    continue
                else:
                    logging.info(f"🔄 파일 크기 다름, 다시 다운로드: {file.display_name}")
                    await send_telegram_message(f"{course_name} 강의 {file.display_name} 파일 크기가 다름")
            else:
                logging.info(f"⬇️ 새 파일 다운로드: {file.display_name}")
                # await send_telegram_message(f"{course_name} 강의 {file.display_name} 파일 다운로드")

            file.download(save_path)

    # 데이터베이스에 저장
    course_db.set_database(course_list)
    assignment_db.set_database(assignment_list)
    lecture_db.set_database(lecture_list)

    # 데이터 변경 감시
    assignment_watcher = DatabaseWatcher(assignment_db)
    lecture_watcher = DatabaseWatcher(lecture_db)

    # 과제 업데이트 감지
    new_assignments = assignment_watcher.check_for_update()
    if new_assignments:
        logging.info("[New Assignments Detected]")
        for row in new_assignments:
            logging.info(f"과제 ID: {row[1]}, 과목명: {row[2]}, 과제명: {row[3]}")
            await send_telegram_message(f"{row[2]} 과목에 새로운 과제 {row[3]}이 등록됨")

    # 강의자료 업데이트 감지
    new_lectures = lecture_watcher.check_for_update()
    if new_lectures:
        logging.info("[New Lectures Detected]")
        for row in new_lectures:
            logging.info(f"과목명: {row[2]}, 파일명: {row[3]}")
            await send_telegram_message(f"{row[2]} 과목에 새로운 강의자료 {row[3]}이 등록됨")

async def loop_main():
    while True:
        now = datetime.datetime.now()
        current_hour = now.hour

        # 새벽 2시~6시 사이에는 동작 금지
        if 2 <= current_hour < 6:
            logging.info(f"🛌 현재 {current_hour}시: 휴식 시간입니다. 1시간 후 다시 확인합니다.")
            await asyncio.sleep(3600)
            continue

        try:
            logging.info(f"작업 시작 ({now.strftime('%Y-%m-%d %H:%M:%S')})")
            await main()
            logging.info(f"작업 완료 ({now.strftime('%Y-%m-%d %H:%M:%S')})")
        except Exception as e:
            error_message = traceback.format_exc()
            logging.error(f"에러 발생: {error_message}")
            await send_telegram_message(f"❗ LMS Bot 에러 발생: {e}")

        await asyncio.sleep(3600)  # 1시간 대기

if __name__ == "__main__":
    asyncio.run(loop_main())
