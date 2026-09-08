# Alpha Intelligence Feed 터미널 (X VIP 모니터링 & 1차 소스 뉴스)

미국의 주요 AI & 정책 핵심 인물(**Tibo, Sam Altman, Elon Musk, Donald Trump**)의 신규 트윗을 실시간 감지하고, **IT · AI 원천 기술 및 대한민국 경제 정책(상법 개정안, 금융위 등) 1차 원문 뉴스**를 텔레그램으로 왜곡 없이 직송하는 100% 무료 상용화 수준의 모니터링 엔진입니다.

---

## 🌟 핵심 기능 및 수집 소스

### 1. 글로벌 오피니언 리더 & VIP 트윗 (0초 지연)
* 🤖 **@thsottiaux (Tibo)**: Astra & 프론티어 AI 인프라
* 🧠 **@sama (Sam Altman)**: OpenAI CEO & 글로벌 AI 혁신
* ⚡ **@elonmusk (Elon Musk)**: Tesla, xAI, 빅테크 리더
* 🏛️ **@realDonaldTrump (Donald J. Trump)**: 미국 경제 정책, 관세(Tariff), 글로벌 거시 규제
* *(언제든 텔레그램에서 `/add_user <아이디>` 로 추가 가능)*

### 2. IT · AI & 경제 정책 1차 소스 수집 엔진
* 🏛️ **대한민국 경제 정책 & 상법 개정안**: 국회 의안 발의안, 금융위원회, 공정거래위원회 실시간 정책 브리핑
* 🤖 **ArXiv CS.AI 공식 API**: 최신 인공지능 원천 연구 논문 원문 초록
* ⚡ **TechCrunch AI**: 글로벌 실리콘밸리 테크 속보

### 3. 초강력 차등 경보 (Siren & Pin)
* **일반 트윗 / 뉴스**: 텔레그램 마크다운 깔끔한 전송
* **긴급 키워드 감지 시** (`reset`, `상법`, `자본시장`, `반독점`, `tariff`, `sec`, `openai`, `gpt` 등):
  * 🚨 텔레그램 메시지 상단 강제 고정(Pin)으로 푸시 강제 울림
  * 스마트폰 진동을 연타로 울리는 2차 리마인더 연속 발송
  * ntfy 연동 시 **안드로이드 방해금지(Do Not Disturb) 모드를 무시하고 최대 볼륨 사이렌** 경보

---

## 📱 텔레그램 대화형 명령어

봇과 대화하며 실시간으로 설정을 제어할 수 있습니다:

| 명령어 | 설명 | 예시 |
|---|---|---|
| `/celebs` | 현재 감시 중인 VIP 리더 목록 및 분야 확인 | `/celebs` |
| `/list` | 감시 계정, 긴급 키워드, 뉴스 피드 상태 확인 | `/list` |
| `/news_toggle` | IT/경제 정책 1차 뉴스 피드 ON / OFF 전환 | `/news_toggle` |
| `/test` (또는 `/check`) | 모든 7개 소스에서 최신 원문 1건씩 즉시 실시간 수신 점검 | `/test` |
| `/add_user <계정>` | 감시할 X 계정 추가 | `/add_user vitalikbuterin` |
| `/del_user <계정>` | 감시 계정 제거 | `/del_user thsottiaux` |
| `/add_keyword <단어>` | 긴급 사이렌 키워드 추가 | `/add_keyword 관세` |
| `/del_keyword <단어>` | 긴급 키워드 제거 | `/del_keyword 관세` |
| `/status` | 봇 업타임, 누적 트윗/뉴스 기록 수 확인 | `/status` |
| `/interval <초>` | 트위터 모니터링 주기 변경 (기본 30초) | `/interval 20` |
| `/set_ntfy <토픽>` | 안드로이드 방해금지 무시 사이렌 연동 | `/set_ntfy my_alpha_feed` |

---

## 🚀 3분 만에 시작하기 (로컬 실행)

1. **텔레그램 토큰 입력 ([config.json](file:///c:/Users/holiy/Downloads/test_app/test_tibo/config.json))**:
   - `@BotFather`에게서 발급받은 토큰을 `bot_token`에 입력합니다.
2. **실행**:
   ```bash
   python bot.py
   ```
3. **연동 확인**:
   - 텔레그램 봇 채팅방에 `/start`를 전송하면 내 계정이 자동 등록되며, `/test`로 사이렌 알림을 즉시 테스트할 수 있습니다.

---

## ☁️ 24시간 365일 무료 클라우드 배포 (Render.com)

컴퓨터를 꺼두어도 24시간 내내 클라우드에서 실시간 모니터링이 유지됩니다.  
상세 가이드는 **[RENDER_DEPLOY.md](file:///c:/Users/holiy/Downloads/test_app/test_tibo/RENDER_DEPLOY.md)**를 확인해주세요.

