# LMS 알림 봇

금오공과대학교 LMS(Canvas)에서 새로운 강의자료 또는 과제가 등록될 때,  
**자동으로 Telegram 알림**을 보내주는 프로그램입니다.

## 주요 기능

- LMS(Canvas)에서 강의자료 및 과제 목록 수집
- **새로운 강의자료 또는 과제, 공지사항 등록 감지**
- 변경사항이 있을 경우 **Telegram 채팅방으로 자동 알림**
- **미제출된 과제 기한을 D-3, D-1, D-day로 나누어 알림**
- **강의자료를 로컬 디렉터리에 분류 저장**
- 강의자료 파일 중복/변경 감지 및 다운로드 최적화
- 과제 및 강의자료를 SQLite 데이터베이스에 저장 및 관리
- **새벽 2시 ~ 6시 동안 자동 휴식 모드**

## 설치 및 실행 방법

### 1. 필수 환경

- Python 3.10 이상
- Linux 또는 Windows 운영체제
- Canvas LMS API 접근 권한
- Telegram Bot API 접근 권한
- 환경변수 설정 필요

### 2. 필요 라이브러리 설치

```bash
pip install python-telegram-bot canvasapi
```

### 3. 환경변수 설정

다음 환경변수를 설정해야 합니다.

| 환경변수 이름 | 설명 |
|:---|:---|
| `TELEGRAM_TOKEN` | 텔레그램 봇 토큰 |
| `CHAT_ID` | 알림을 보낼 텔레그램 채팅방 ID |
| `LMS_API_KEY` | Canvas LMS API 토큰 |


> 참고: LMS API 키 발급 방법
> ![API 발급 화면](https://github.com/user-attachments/assets/8007c84a-fd9a-42c0-baac-dde5fbda18db)  
> - LMS 사이트에서 **계정 → 설정**으로 이동합니다.  
> - API 키(토큰)를 발급받습니다.  
> - 발급받은 API 키를 환경변수 `LMS_API_KEY`로 등록합니다.  

#### Linux 예시 (.bashrc 또는 .zshrc)

```bash
export TELEGRAM_TOKEN="여기에_텔레그램_토큰"
export CHAT_ID="여기에_채팅방_ID"
export LMS_API_KEY="여기에_LMS_API_키"
```

#### Windows 예시 (cmd)
```cmd
set TELEGRAM_TOKEN=여기에_텔레그램_토큰
set CHAT_ID=여기에_채팅방_ID
set LMS_API_KEY=여기에_LMS_API_키
```
### 4. 파일 및 폴더 구조

```plaintext
├── LMS.db                  # SQLite 데이터베이스
├── Univ/                   # 강의자료 저장 폴더
│   ├── (과목명)/
│       ├── 강의자료/
│       ├── 기타파일/
├── lms.log                  # 프로그램 실행 로그
├── main.py                  # 메인 코드 파일
```
> Windows와 Linux 모두 지원하며, 운영체제에 따라 경로가 자동 설정됩니다.

### 5. 실행 방법
```bash
python main.py
```
- 프로그램은 1시간마다 LMS를 체크하여 새로운 과제나 강의자료를 탐지합니다.
- 새벽 2시 ~ 6시 사이에는 자동으로 휴식합니다.

### 동작 흐름
1. LMS(Canvas)에서 공지사항, 과목, 과제, 강의자료 정보를 수집합니다.
2. SQLite 데이터베이스(LMS.db)에 저장합니다.
3. 새로 추가된 과제나 강의자료가 있는 경우 감지합니다.
4. 새로운 항목이 있으면 Telegram 채팅방으로 알림을 전송합니다.
5. 미제출된 과제가 얼마 남지 않았을 경우 D-3, D-1, D-day에 나눠서 Telegram으로 알림을 전송합니다.
6. 강의자료는 과목별로 분류하여 로컬 디렉터리에 저장합니다.

### 로깅
- 프로그램의 모든 로그는 lms.log 파일에 기록됩니다.
- 에러 발생 시 텔레그램으로 에러 메시지를 전송합니다.

### 주의사항
- Canvas LMS API의 토큰 만료 주기를 확인하여 주기적으로 갱신해야 할 수 있습니다.
- Telegram Bot은 사용자가 직접 생성해야 하며, chat_id를 정확히 설정해야 정상 작동합니다.
- Linux 서버 운영 시 crontab이나 systemd를 이용해 백그라운드 자동 실행을 설정할 수 있습니다.

### 서버 동기화 (선택사항)
프로그램이 저장한 강의자료(Univ/ 폴더)를 다른 서버와 동기화하고 싶다면,
rsync를 이용하여 자동 동기화를 설정할 수 있습니다.

예시 명령어:
```bash
rsync -avzu --progress \
  root@remote_server:/remote/path/to/Univ/ /local/path/to/Univ/
```

- -a: archive 모드 (파일 권한, 타임스탬프 유지)

- -v: verbose 모드 (진행상황 출력)

- -z: 전송 중 압축

- -u: 최신 파일만 업데이트

- --progress: 전송 진행 상황 표시

> rsync를 cron 작업에 등록하면 정기적으로 자동 동기화가 가능합니다.

### crontab 예시
```bash
# 매일 새벽 4시에 Univ 폴더를 서버로 동기화
0 4 * * * rsync -avzu --progress root@remote_server:/remote/path/to/Univ/ /local/path/to/Univ/
```
> 서버와의 SSH 인증을 위해 SSH 키를 설정해두는 것을 추천합니다.
# LMS_Anouncement_actions
# LMS_Anouncement_actions
