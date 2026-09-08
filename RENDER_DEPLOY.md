# Render.com 무료 24시간 클라우드 호스팅 가이드

Render.com의 무료 플랜(월 750시간 무료 = 1개 서비스 30일 24시간 풀가동)을 통해 **내 컴퓨터를 꺼두어도 24시간 365일 무중단**으로 봇을 실행하는 방법입니다.

---

## 1단계: GitHub에 코드 올리기

현재 폴더의 코드를 본인의 GitHub 저장소에 업로드합니다.  
(`.gitignore` 덕분에 비밀 파일인 `config.json`은 자동으로 제외되므로 안전합니다.)

터미널(PowerShell 또는 CMD)에서 다음 명령을 순서대로 실행합니다:

```bash
# 1. git 초기화 (아직 안 되어 있는 경우)
git init
git add .
git commit -m "Deploy to Render"

# 2. GitHub에서 새 저장소(New repository) 생성 후 원격 주소 연결
# (아래 URL을 본인의 GitHub 저장소 주소로 변경해주세요)
git branch -M main
git remote add origin https://github.com/내아이디/내저장소이름.git
git push -u origin main
```

---

## 2단계: Render.com 가입 및 로그인

1. **[render.com](https://render.com)** 에 접속합니다.
2. 우측 상단 **"GET STARTED"** 또는 **"Sign In"**을 누르고 **"GitHub" 계정으로 로그인**합니다.

---

## 3단계: 새 웹 서비스(New Web Service) 생성

1. Render 대시보드 우측 상단 파란색 **"New +"** 버튼 클릭 $\rightarrow$ **"Web Service"** 선택
2. **"Build and deploy from a Git repository"** 선택 후 **Next** 클릭
3. 방금 올린 본인의 **GitHub 저장소(Repository)**를 찾아 오른쪽에 있는 **"Connect"** 버튼 클릭

---

## 4단계: 서비스 세부 설정

화면의 입력창들을 아래와 같이 설정합니다:

| 항목 (Field) | 설정 값 | 비고 |
|---|---|---|
| **Name** | `alpha-feed-bot` | 원하는 이름 아무거나 입력 |
| **Region** | `Singapore` (또는 `Oregon`) | 한국과 가까운 싱가포르 추천 |
| **Branch** | `main` | 기본값 유지 |
| **Runtime** | **`Docker`** | 저장소의 Dockerfile 자동 감지 |
| **Instance Type** | **`Free` ($0/month)** | ⭐️ 반드시 Free 선택! |

---

## 5단계: 환경변수(Environment Variables) 등록 (중요 ⭐️)

페이지를 아래로 스크롤하여 **"Environment Variables"** 섹션에서 **"Add Environment Variable"** 버튼을 눌러 다음 2개 값을 등록합니다:

| Key (변수명) | Value (값) | 설명 |
|---|---|---|
| `BOT_TOKEN` | `YOUR_TELEGRAM_BOT_TOKEN_HERE` | @BotFather에게서 발급받은 봇 토큰 (필수) |
| `CHAT_ID` | `YOUR_TELEGRAM_CHAT_ID_HERE` | 본인의 텔레그램 Chat ID (숫자, 필수) |
| `OPENROUTER_API_KEY` | *(선택 사항)* | [OpenRouter](https://openrouter.ai/keys) API Key (무료 :free 모델 1시간 주기 자동 선정 3줄 요약용, 텔레그램에서 키를 채팅창에 바로 전송해도 등록 가능) |

*(선택사항으로 `PRIORITY_KEYWORDS`나 `MONITORED_USERS`도 지정할 수 있으나, 기본값으로 머스크, 올트먼, 트럼프, 티보, 상법 등이 이미 내장되어 있습니다.)*

---

## 6단계: 배포 시작 및 24시간 구동 확인

1. 맨 아래 **"Deploy Web Service"** 버튼을 클릭합니다.
2. 약 1~2분 정도 빌드가 진행된 후, 상단 상태가 주황색에서 **초록색 `Live`** 로 변경됩니다!
3. 스마트폰 텔레그램 앱에서 내 봇(`@hyperalertfeed_bot`)에게 **`/status`** 를 전송해 보세요:
   > *"🟢 Alpha Terminal 정상 구동 중 (가동 시간: 35초 ...)"*

---

### 💡 [보너스] Render 무료 티어 24시간 슬립 방지 기능 내장
* Render 무료 티어는 원래 15분간 외부 접속이 없으면 잠드는(Spin-down) 특성이 있습니다.
* 하지만 **저희 코드(`bot.py`)에 10분마다 스스로 핑을 보내 깨어있는 '셀프 킵얼라이브(Self Keep-Alive)' 기능이 이미 내장**되어 있으므로, 아무런 추가 작업 없이도 24시간 365일 잠들지 않고 계속 실시간 감시를 수행합니다!
