# 선상24 쭈꾸미 낚시배 모니터링 (GitHub Actions)

인천/경기/충남, 9월 19~20일, 오후배(12~19시 출항), 2명 이상 남은자리, 조황정보 10건 이상
조건에 맞는 배가 새로 나타나면 텔레그램으로 알려줍니다. GitHub의 클라우드에서 5분마다
자동 실행되므로, 맥북을 켜둘 필요가 없습니다.

## 설정 방법 (최초 1회)

1. **GitHub에 새 저장소 만들기**
   - github.com 에서 New repository (Private로 만들어도 됩니다)
   - 이 폴더(`sunsang24-monitor`) 안의 파일 전체를 그대로 그 저장소에 업로드
     (`.github/workflows/monitor.yml`, `monitor_sunsang24.py`, `seen_schedules.json` 구조 그대로 유지)

2. **텔레그램 봇 토큰 등록 (Secrets)**
   - 저장소 페이지 > Settings > Secrets and variables > Actions > New repository secret
   - `TELEGRAM_BOT_TOKEN` 이름으로 봇 토큰 저장
   - `TELEGRAM_CHAT_ID` 이름으로 그룹/개인 chat_id 저장

3. **활성화 확인**
   - 저장소 Actions 탭에서 "sunsang24-monitor" 워크플로우가 보이면 정상
   - "Run workflow" 버튼으로 수동 실행해서 텔레그램 메시지가 오는지(또는 "조건에 맞는 새 항목
     없음" 로그가 뜨는지) 확인

## 조건 바꾸고 싶을 때

`monitor_sunsang24.py` 상단의 다음 값을 수정하면 됩니다.

- `SDATE`: 확인할 날짜 (예: `"2026-09-19,2026-09-20"`)
- `MIN_SEATS`: 최소 남은자리 수
- `MIN_START_HOUR` / `MAX_START_HOUR`: 출항 시간대 범위 (24시간제)
- `MIN_BOARD_COUNT`: 최소 조황정보(이용 이력) 건수

## 참고 / 한계

- GitHub의 무료 스케줄 실행은 서버 상황에 따라 몇 분 정도 지연될 수 있습니다(칼같이 5분은
  아닐 수 있음).
- 저장소가 60일 이상 활동이 없으면 GitHub이 예약 실행을 자동으로 비활성화합니다. 그럴 땐
  Actions 탭에서 다시 켜주시면 됩니다.
- 예약이 끝난 뒤(9월 20일 이후)에는 워크플로우를 꺼두시는 게 좋습니다. 저장소 Settings >
  Actions > Disable actions 에서 끌 수 있습니다.
