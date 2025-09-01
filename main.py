import os
import sqlite3
import datetime
from canvasapi import Canvas
import telegram
import logging
import asyncio
import traceback
from bs4 import BeautifulSoup

telegram_token = os.environ.get('TELEGRAM_TOKEN')
chat_id = os.environ.get('CHAT_ID')
if telegram_token is None or chat_id is None:
    logging.error("환경변수 'TELEGRAM_TOKEN' 또는 'CHAT_ID'가 설정되지 않았습니다.")
    raise ValueError("환경변수 'TELEGRAM_TOKEN' 또는 'CHAT_ID'가 설정되지 않았습니다.")

windows_path = r'C:\Users\barah\Desktop\Univ' # windows에서 실행 시
linux_path = '/Univ/Univ/1-2/' # linux에서 실행 시
linux_parent_path = '/Univ/' # 로그 파일 저장 위치
path = windows_path if os.name == 'nt' else linux_path # 사용 운영체제에 따라 경로 설정
parent_path = linux_parent_path if os.name != 'nt' else windows_path
logging.basicConfig(filename=os.path.join(linux_parent_path, 'lms.log'), level=logging.INFO, format='%(asctime)s:%(levelname)s:%(message)s', encoding='utf-8')
logging.info("LMS Bot 시작")

db_path = os.path.join(linux_parent_path, "LMS.db")
API_URL = "https://canvas.kumoh.ac.kr"
API_KEY = os.environ.get('LMS_API_KEY')
if API_KEY is None:
    logging.error("환경변수 'LMS_API_KEY'가 설정되지 않았습니다.")
    raise ValueError("환경변수 'LMS_API_KEY'가 설정되지 않았습니다.")

bot = telegram.Bot(token=telegram_token)

async def send_telegram_message(message):
    await bot.send_message(chat_id=chat_id, text=message)

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
        for course_id, course_name, course_code in tr_list:
            cur.execute("SELECT * FROM course WHERE course_id=:Id", {"Id": course_id})
            if cur.fetchone() is None:
                cur.execute("INSERT INTO course (course_id, course_name, course_code) VALUES (?, ?, ?)",
                            (course_id, course_name, course_code))
        con.close()

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
                        description TEXT NULL)""")
        for assignment_id, course_id, course_name, assignment_name, start_date, end_date, description in tr_list:
            cur.execute("SELECT * FROM assignment WHERE assignment_id=:Id", {"Id": assignment_id})
            if cur.fetchone() is None:
                cur.execute("""INSERT INTO assignment 
                            (assignment_id, course_id, course_name, assignment_name, start_date, end_date, description) 
                            VALUES (?, ?, ?, ?, ?, ?, ?)""",
                            (assignment_id, course_id, course_name, assignment_name, start_date, end_date, description))
        con.close()

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
        for course_id, course_name, file_name, file_size in tr_list:
            file_name = file_name.replace("'", "''")
            cur.execute("SELECT * FROM lecture WHERE course_id=:Id AND file_name=:File",
                        {"Id": course_id, "File": file_name})
            if cur.fetchone() is None:
                cur.execute("""INSERT INTO lecture 
                            (course_id, course_name, file_name, file_size) 
                            VALUES (?, ?, ?, ?)""",
                            (course_id, course_name, file_name, file_size))
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

async def main(course_db, assignment_db, lecture_db):
    canvas = Canvas(API_URL, API_KEY)
    courses = canvas.get_courses()

    course_list, assignment_list, lecture_list = [], [], []

    for course in courses:
        course_name = course.name.split('-')[0]
        
        course_code = '-'.join(course.course_code.split('-')[1:])
        course_list.append((course.id, course_name, course_code))

        for assignment in course.get_assignments():
            assignment_list.append((
                assignment.id,
                course.id,
                course_name,
                assignment.name,
                assignment.unlock_at,
                assignment.due_at,
                assignment.description
            ))

        for file in course.get_files():
            if file.locked_for_user == True:
                continue
            lecture_list.append((course.id, course_name, file.display_name, file.size))

            if list(course.get_files()):
                make_dir(os.path.join(path, course_name))

            sub_dir = '강의자료' if any(ext in file.display_name.lower() for ext in ['pdf', 'ppt', 'doc', 'hwp']) else '기타파일'
            make_dir(os.path.join(path, course_name, sub_dir))

            save_path = os.path.join(path, course_name, sub_dir, file.display_name)
            if os.path.exists(save_path) and file.size is not None:
                if os.path.getsize(save_path) == file.size:
                    logging.info(f"✅ 이미 존재하는 파일이며 크기 동일: {file.display_name}, 다운로드 생략")
                    continue
                else:
                    logging.info(f"🔄 파일 크기 다름, 다시 다운로드: {file.display_name}")
                    await send_telegram_message(f"{course_name} 강의 {file.display_name} 파일 크기가 다름")
            else:
                logging.info(f"⬇️ 새 파일 다운로드: {file.display_name}")
                await send_telegram_message(f"{course_name} 강의 {file.display_name} 파일 다운로드")
            
            file.download(save_path)

    course_db.set_database(course_list)
    assignment_db.set_database(assignment_list)
    lecture_db.set_database(lecture_list)

async def loop_main():
    course_db = CourseDB(db_path)
    assignment_db = AssignmentDB(db_path)
    lecture_db = LectureDB(db_path)

    assignment_watcher = DatabaseWatcher(assignment_db)
    lecture_watcher = DatabaseWatcher(lecture_db)

    while True:
        now = datetime.datetime.now()
        current_hour = now.hour

        if 2 <= current_hour < 6:
            logging.info(f"🛌 현재 {current_hour}시: 휴식 시간입니다. 1시간 후 다시 확인합니다.")
            await asyncio.sleep(3600)
            continue

        try:
            logging.info(f"작업 시작 ({now.strftime('%Y-%m-%d %H:%M:%S')})")
            await main(course_db, assignment_db, lecture_db)
            logging.info(f"작업 완료 ({now.strftime('%Y-%m-%d %H:%M:%S')})")

            new_assignments = assignment_watcher.check_for_update()
            for row in new_assignments:
                logging.info(f"과제 ID: {row[2]}, 과목명: {row[3]}, 과제명: {row[4]}")
                try:
                    start_time = row[5].strftime("%Y-%m-%d %H:%M:%S") if row[5] else "없음"
                except:
                    start_time = "없음"
                try:
                    end_time = row[6].strftime("%Y-%m-%d %H:%M:%S") if row[6] else "없음"
                except:
                    end_time = "없음"
                description = row[7] if row[7] else "없음"
                soup_description = BeautifulSoup(description, 'html.parser')
                description_text = soup_description.get_text(strip=True)
                description_text = description_text.replace("&nbsp;", "\n")
                await send_telegram_message(f"{row[3]} 과목에 새로운 과제 {row[4]}이 등록됨\n시작일: {start_time}\n마감일: {end_time}\n내용: {description_text}")

            new_lectures = lecture_watcher.check_for_update()
            for row in new_lectures:
                logging.info(f"과목명: {row[2]}, 파일명: {row[3]}")
                # await send_telegram_message(f"{row[2]} 과목에 새로운 강의자료 {row[3]}이 등록됨")

        except Exception as e:
            logging.error(f"에러 발생: {traceback.format_exc()}")
            await send_telegram_message(f"❗ LMS Bot 에러 발생: {e}")

        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(loop_main())
