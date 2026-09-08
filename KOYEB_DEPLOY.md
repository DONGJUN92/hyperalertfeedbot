# Koyeb 무료 24시간 호스팅 배포 가이드

Koyeb의 무료 티어(Eco instance)를 활용하여 **PC를 끈 상태에서도 24시간 365일 무중단으로 실행**되는 실시간 X 모니터링 봇을 배포하는 방법입니다.

---

## 1단계: GitHub에 코드 올리기

현재 폴더의 코드를 본인의 GitHub 저장소에 업로드합니다.
(`.gitignore`가 이미 설정되어 있어 비밀 토큰이 깃허브에 노출되지 않으므로 퍼블릭/프라이빗 상관없이 안전합니다.)

터미널(PowerShell 또는 CMD)에서 다음 명령을 순서대로 실행합니다:

```bash
# 1. git 초기화 (아직 안 되어 있는 경우)
git init
git add .
git commit -m "Initial commit for Koyeb deployment"

# 2. GitHub에서 새 저장소(New repository)를 만든 후 원격 주소 연결
# (아래 URL을 본인의 GitHub 저장소 주소로 변경해주세요)
git branch -M main
git remote add origin https://github.com/내아이디/내저장소이름.git
git push -u origin main
```

---

## 2단계: Koyeb 가입 및 서비스 생성

1. **[koyeb.com](https://www.koyeb.com/)** 접속 후 **GitHub 계정으로 로그인(Sign in with GitHub)**합니다.
2. 상단 또는 대시보드에서 **"Create Service"** 버튼을 클릭합니다.
3. 배포 방식(Deployment Method)에서 **"GitHub"**를 선택합니다.
4. 방금 올린 GitHub 저장소를 선택합니다.

---

## 3단계: 빌드 및 인스턴스 설정

* **Builder**: `Dockerfile` (저장소에 있는 Dockerfile이 자동으로 감지됩니다.)
* **Instance Type**: **`Eco - Free`** (또는 `nano` 무료 플랜) 선택
* **Ports**:
  * Port: `8000`
  * Protocol: `HTTP`
  * Path: `/`

---

## 4단계: 환경변수(Environment Variables) 등록 (중요 ⭐️)

화면 아래쪽 **"Environment variables"** 섹션에서 **"Add variable"**을 눌러 다음 값들을 추가합니다:

| 변수명 (Name) | 값 (Value) | 설명 |
|---|---|---|
| `BOT_TOKEN` | `7123456789:AAFx...` | 텔레그램 `@BotFather`에게서 발급받은 토큰 |
| `MONITORED_USERS` | `thsottiaux` | 감시할 계정 (여러 개면 쉼표로 구분: `thsottiaux,elonmusk`) |
| `PRIORITY_KEYWORDS` | `reset` | 긴급 키워드 (여러 개면 쉼표로 구분: `reset,urgent`) |
| `CHECK_INTERVAL` | `30` | 감시 주기 (초 단위, 기본 30초) |
| `NTFY_TOPIC` | `내_토픽명` | (선택사항) 안드로이드 방해금지 무시 사이렌용 토픽 |

> 💡 **`CHAT_ID`는 비워두셔도 됩니다!**  
> 배포 완료 후 텔레그램 봇 대화방에서 `/start`를 한 번만 전송하면 내 계정 ID가 자동으로 감지되어 수신처로 등록됩니다.

---

## 5단계: 배포 완료 및 동작 확인

1. 페이지 하단의 **"Deploy"** 버튼을 누릅니다.
2. 약 1~2분 뒤 상태가 **`Healthy`** (초록색)로 바뀌면 배포가 성공한 것입니다!
3. 스마트폰 텔레그램 앱에서 내 봇에게 **`/status`** 또는 **`/test`** 를 전송해보세요.
   * `🟢 Koyeb 호스팅 정상 구동 중` 메시지와 함께 테스트 알림이 울리면 모든 설정이 완료된 것입니다.

이제 컴퓨터를 끄거나 스마트폰이 잠겨 있어도 Koyeb 클라우드에서 24시간 실시간 감시가 계속됩니다.
