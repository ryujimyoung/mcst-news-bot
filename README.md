# 문체부 보도 텔레그램 알림 봇

Google News Korea RSS에서 `문화체육관광부`, `문체부` 관련 새 기사를 60초마다 확인해 텔레그램으로 보냅니다. 전송 대상은 Google 뉴스에 표시되는 모든 언론사입니다. 이미 보낸 기사 링크는 SQLite에 저장해 중복 전송하지 않습니다. 최초 실행은 기존 기사를 저장만 하므로 과거 기사 폭주가 없습니다.

조선일보, 동아일보, 한겨레, 경향신문은 공식 RSS도 직접 확인합니다. 이 네 매체는 Google 뉴스보다 RSS 결과를 우선합니다. 중앙일보은 현재 일반기사 공개 RSS를 안정적으로 사용할 수 없어 Google 뉴스 수집으로 보완합니다.

## 준비

1. Telegram에서 `@BotFather`에게 `/newbot`을 보내 봇을 만들고 토큰을 받습니다.
2. 만든 봇에 메시지를 한 번 보냅니다. (그룹에 보낼 경우 그룹에 봇을 초대하고 메시지를 보냅니다.)
3. 브라우저에서 `https://api.telegram.org/bot토큰/getUpdates`를 열어 응답의 `chat.id`를 찾습니다. 그룹 채팅 ID는 보통 음수입니다.
4. PowerShell에서 아래처럼 실행합니다. 비밀 값은 코드나 Git에 넣지 마세요.

```powershell
cd C:\Users\rjihy\Documents\Codex\2026-09-04\ans\outputs
$env:TELEGRAM_BOT_TOKEN = 'BotFather에서 받은 토큰'
$env:TELEGRAM_CHAT_ID = 'chat.id'
.\start_mcst_bot.ps1
```

처음 실행하면 기존 검색 결과를 기준값으로 저장하고, 이후 새 기사부터 전송합니다. 멈추려면 창에서 `Ctrl+C`를 누릅니다.

기본 설정은 발행 후 30분 이내 기사만 보냅니다. 이로써 오전 기사가 저녁에 뒤늦게 전송되는 일을 줄입니다. 단, Google 뉴스 수집 자체가 늦는 기사는 보내지지 않을 수 있습니다. 더 넓게 보려면 PowerShell에서 실행 전에 `$env:MAX_ARTICLE_AGE_MINUTES = '60'`처럼 설정할 수 있습니다.

## PC를 꺼도 받기: GitHub Actions

`outputs` 폴더를 GitHub 저장소로 올리면, GitHub가 5분마다 한 번씩 봇을 실행할 수 있습니다. GitHub Actions는 1분 간격 실행을 지원하지 않으며, 혼잡할 때 실행이 조금 지연될 수 있습니다.

1. GitHub에서 새 저장소를 만듭니다. 무료로 계속 돌리려면 공개 저장소가 가장 단순합니다. 봇 토큰은 저장소에 넣지 않습니다.
2. 이 `outputs` 폴더의 파일과 `.github/workflows/mcst-news-bot.yml`을 저장소 최상위에 올립니다.
3. 저장소에서 **Settings → Secrets and variables → Actions → New repository secret**으로 이동합니다.
4. 아래 두 Secret을 만듭니다. 값은 기존 값이며, 따옴표 없이 입력합니다.
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
5. **Actions** 탭에서 `MCST Telegram news monitor` 워크플로를 열고 **Run workflow**를 한 번 누릅니다. 첫 실행은 기존 기사를 기록만 하고, 이후부터 새 기사만 보냅니다.

전송 기록 파일 `mcst_news_seen.sqlite3`은 워크플로가 자동으로 저장소에 갱신합니다. 이 파일에는 Telegram 토큰이나 채팅 ID가 들어가지 않습니다.

## 자동 실행

PC가 켜져 있을 때 계속 받으려면, 위 명령을 실행한 PowerShell 창을 열어 둡니다. 상시 실행이 필요하면 Windows 작업 스케줄러에서 `로그온할 때` 실행되도록 등록하세요. 토큰을 작업 인수에 직접 넣는 대신 Windows 사용자 환경 변수 `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`로 저장하는 편이 안전합니다.

## 긍·부정 표기

제목의 명시적 키워드로 `🟢 긍정`, `🔴 부정`, `⚪ 중립`을 자동 분류합니다. 기사 전체의 사실 판정이나 여론 분석이 아니므로, 특히 논란성 이슈에서는 검토용 신호로만 사용하세요. `mcst_telegram_news_bot.py`의 `POSITIVE`, `NEGATIVE` 목록을 조직의 기준에 맞게 조정할 수 있습니다.
