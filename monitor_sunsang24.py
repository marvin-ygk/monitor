#!/usr/bin/env python3
"""
선상24(sunsang24.com) 주꾸미 낚시배 모니터링 스크립트 (GitHub Actions용)

인천/경기/충남 지역, 9월 19~20일, 오후배, 2명 이상 남은자리인 배가
새로 나타나면 텔레그램으로 알려줍니다.

표준 라이브러리만 사용하므로 별도 설치(pip install) 없이 실행됩니다.
"""
import json
import os
import urllib.request
import urllib.parse

# ===== 설정 (필요에 맞게 수정하세요) =====

# 지역 코드: 인천 + 경기 + 충남
AREA = "-405,509,510,511,2519,514,515,-404,504,505,507,506,-403,501,497,499,496,500,498"

FISH = "주꾸미"                    # 어종
SDATE = "2026-09-19,2026-09-20"    # 확인할 날짜 (콤마로 구분)

MIN_SEATS = 2           # 최소 남은자리 (2명 = 아버지+아들)
MIN_START_HOUR = 12     # 오후배 기준: 이 시각 "이후" 출항만 (24시간제)
MAX_START_HOUR = 19     # 오후배 기준: 이 시각 "이전" 출항만
MIN_BOARD_COUNT = 10    # 최소 조황정보(이용 이력) 건수 — 신뢰도 필터

# 텔레그램 설정: GitHub Actions에서는 Secrets로 주입됩니다.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# 이미 알림 보낸 항목을 기억해두는 파일 (중복 알림 방지)
# GitHub Actions 워크플로우가 매 실행 후 이 파일을 저장소에 커밋해서 상태를 유지합니다.
SEEN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "seen_schedules.json")

API_URL = "https://api.sunsang24.com/ship/list"


def fetch_listings():
    """선상24 API에서 현재 조건에 맞는 전체 스케줄 목록을 가져온다."""
    params = {
        "area": AREA,
        "area_text": "",
        "area_type": "area",
        "fish": FISH,
        "is_possible": "is_possible",
        "page": 1,
        "sdate": SDATE,
        "type": "general",
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.load(resp)
    return data.get("list", [])


def matches_criteria(item):
    """사용자가 정한 조건(자리/오후시간/이력)을 만족하는지 확인."""
    if item.get("schedule_status_code") != "ING":  # 예약마감이면 제외
        return False
    if item.get("remain_embarkation_num", 0) < MIN_SEATS:
        return False
    stime = item.get("stime") or "23:59:00"
    try:
        hour = int(stime.split(":")[0])
    except (ValueError, IndexError):
        return False
    if not (MIN_START_HOUR <= hour < MAX_START_HOUR):
        return False
    if item.get("board_write_num", 0) < MIN_BOARD_COUNT:
        return False
    return True


def load_seen():
    if os.path.exists(SEEN_FILE):
        with open(SEEN_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen(seen):
    with open(SEEN_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(seen), f, ensure_ascii=False, indent=0)


def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("텔레그램 토큰/chat_id가 설정되지 않아 메시지를 보내지 않습니다.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
    req = urllib.request.Request(url, data=data)
    urllib.request.urlopen(req, timeout=15)


def main():
    listings = fetch_listings()
    seen = load_seen()
    new_seen = set(seen)
    new_matches = []

    for item in listings:
        if not matches_criteria(item):
            continue
        key = str(item["schedule_no"])
        new_seen.add(key)
        if key not in seen:
            new_matches.append(item)

    if new_matches:
        lines = ["🎣 오후배 2자리 이상 - 새 낚시배가 나왔습니다!\n"]
        for item in new_matches:
            ship = item["ship"]["name"]
            date = item["sdate"]
            stime = (item.get("stime") or "")[:5]
            etime = (item.get("etime") or "")[:5]
            port = item.get("port_name", "")
            seats = item.get("remain_embarkation_num", 0)
            board = item.get("board_write_num", 0)
            price = item.get("price", 0)
            lines.append(
                f"- {ship} ({port})\n"
                f"  {date} {stime}~{etime} / 남은자리 {seats}명 / "
                f"조황정보 {board}건 / 인당 {price:,}원"
            )
        send_telegram("\n".join(lines))
        print(f"알림 전송 완료: {len(new_matches)}건")
    else:
        print("조건에 맞는 새 항목 없음")

    save_seen(new_seen)


if __name__ == "__main__":
    main()
