#!/usr/bin/env python3
"""
선상24(sunsang24.com) 낚시배 모니터링 + 텔레그램 명령 처리 스크립트

기능
1) 조건에 맞는 배가 새로 나타나면 텔레그램으로 알림
2) 텔레그램 그룹에서 보낸 명령어 처리 (/help, /status, /all, /history,
   /date, /seats, /hours, /board)

GitHub Actions에서 5분마다 실행되는 것을 전제로 합니다.
표준 라이브러리만 사용하므로 별도 설치(pip install)가 필요 없습니다.
"""
import json
import os
import urllib.request
import urllib.parse
from datetime import datetime, timedelta, timezone

# ===== 기본 설정 =====

# 지역 코드: 인천 + 경기 + 충남 (선상24 사이트의 내부 지역 코드)
AREA = "-405,509,510,511,2519,514,515,-404,504,505,507,506,-403,501,497,499,496,500,498"
FISH = "주꾸미"

# 명령어를 사용할 수 있는 텔레그램 사용자 ID 목록.
# 비워두면 그룹의 누구나 명령을 쓸 수 있습니다.
# 본인 ID만 넣으려면 예: ALLOWED_USER_IDS = [123456789]
ALLOWED_USER_IDS = []

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")        # 감시 조건 (명령어로 변경 가능)
SEEN_FILE = os.path.join(BASE_DIR, "seen_schedules.json")  # 알림 보낸 스케줄 기록
HISTORY_FILE = os.path.join(BASE_DIR, "history.json")      # 조회 이력
OFFSET_FILE = os.path.join(BASE_DIR, "tg_offset.json")     # 텔레그램 메시지 읽은 위치

API_URL = "https://api.sunsang24.com/ship/list"
KST = timezone(timedelta(hours=9))

DEFAULT_CONFIG = {
    "sdate": "2026-09-19,2026-09-20",
    "min_seats": 2,
    "min_start_hour": 12,
    "max_start_hour": 19,
    "min_board_count": 10,
}

HISTORY_MAX = 30  # 이력 보관 개수


# ===== 파일 읽기/쓰기 =====

def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return default
    return default


def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    cfg.update(load_json(CONFIG_FILE, {}))
    return cfg


# ===== 텔레그램 =====

def tg_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("텔레그램 설정 없음 - 전송 생략")
        print(text)
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": "true",
    }).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=15)
    except Exception as e:
        print(f"텔레그램 전송 실패: {e}")


def tg_get_updates():
    """마지막으로 읽은 이후의 새 메시지를 가져온다."""
    if not TELEGRAM_BOT_TOKEN:
        return []
    offset = load_json(OFFSET_FILE, {}).get("offset", 0)
    params = {"timeout": 0}
    if offset:
        params["offset"] = offset
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?" + urllib.parse.urlencode(params)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.load(resp)
    except Exception as e:
        print(f"텔레그램 수신 실패: {e}")
        return []
    updates = data.get("result", [])
    if updates:
        save_json(OFFSET_FILE, {"offset": updates[-1]["update_id"] + 1})
    return updates


# ===== 선상24 조회 =====

def fetch_listings(sdate):
    params = {
        "area": AREA,
        "area_text": "",
        "area_type": "area",
        "fish": FISH,
        "is_possible": "is_possible",
        "page": 1,
        "sdate": sdate,
        "type": "general",
    }
    url = API_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.load(resp).get("list", [])


def start_hour(item):
    try:
        return int((item.get("stime") or "23:59:00").split(":")[0])
    except (ValueError, IndexError):
        return 99


def matches(item, cfg):
    if item.get("schedule_status_code") != "ING":
        return False
    if item.get("remain_embarkation_num", 0) < cfg["min_seats"]:
        return False
    if not (cfg["min_start_hour"] <= start_hour(item) < cfg["max_start_hour"]):
        return False
    if item.get("board_write_num", 0) < cfg["min_board_count"]:
        return False
    return True


def describe(item):
    return (
        f"- {item['ship']['name']} ({item.get('port_name','')})\n"
        f"  {item.get('sdate','')} {(item.get('stime') or '')[:5]}~{(item.get('etime') or '')[:5]}"
        f" / 남은자리 {item.get('remain_embarkation_num',0)}명"
        f" / 조황 {item.get('board_write_num',0)}건"
        f" / 인당 {item.get('price',0):,}원"
    )


def config_text(cfg):
    return (
        f"감시 날짜: {cfg['sdate']}\n"
        f"최소 인원: {cfg['min_seats']}명\n"
        f"출항 시간대: {cfg['min_start_hour']}시~{cfg['max_start_hour']}시\n"
        f"최소 조황정보: {cfg['min_board_count']}건"
    )


def now_kst():
    return datetime.now(KST).strftime("%m-%d %H:%M")


# ===== 명령어 처리 =====

HELP_TEXT = (
    "🎣 사용 가능한 명령어\n\n"
    "/status - 현재 설정 + 지금 조건에 맞는 배 보기\n"
    "/all - 조건 무시하고 자리 있는 배 전부 보기\n"
    "/history - 최근 조회 이력 보기\n"
    "/date 2026-09-26,2026-09-27 - 감시 날짜 변경\n"
    "/seats 2 - 최소 인원 변경\n"
    "/hours 12 19 - 출항 시간대 변경 (12시~19시)\n"
    "/board 10 - 최소 조황정보 건수 변경\n"
    "/help - 이 도움말\n\n"
    "※ 5분마다 실행되므로 명령 후 응답까지 최대 5~15분 걸릴 수 있습니다."
)


def handle_command(text, cfg):
    """명령어를 처리하고 (응답문구, 설정변경여부)를 돌려준다."""
    parts = text.strip().split()
    cmd = parts[0].lower().split("@")[0]
    args = parts[1:]

    if cmd == "/help":
        return HELP_TEXT, False

    if cmd == "/status":
        try:
            items = fetch_listings(cfg["sdate"])
        except Exception as e:
            return f"조회 실패: {e}", False
        hits = [i for i in items if matches(i, cfg)]
        msg = f"📋 현재 설정\n{config_text(cfg)}\n\n"
        if hits:
            msg += f"조건에 맞는 배 {len(hits)}건:\n" + "\n".join(describe(i) for i in hits)
        else:
            msg += "조건에 맞는 배가 현재 없습니다."
        return msg, False

    if cmd == "/all":
        try:
            items = fetch_listings(cfg["sdate"])
        except Exception as e:
            return f"조회 실패: {e}", False
        avail = [i for i in items
                 if i.get("schedule_status_code") == "ING"
                 and i.get("remain_embarkation_num", 0) >= 1]
        avail.sort(key=lambda i: (i.get("sdate", ""), i.get("stime", "")))
        if not avail:
            return f"[{cfg['sdate']}] 자리 있는 배가 하나도 없습니다.", False
        msg = f"🔎 자리 있는 배 전체 {len(avail)}건 ({cfg['sdate']})\n"
        msg += "(조건 무시, 1자리 이상 전부)\n\n"
        msg += "\n".join(describe(i) for i in avail)
        return msg, False

    if cmd == "/history":
        hist = load_json(HISTORY_FILE, [])
        if not hist:
            return "아직 조회 이력이 없습니다.", False
        msg = f"📊 최근 조회 이력 (최근 {len(hist)}회)\n\n"
        for h in reversed(hist[-15:]):
            msg += f"{h['time']} - 조건충족 {h['matched']}건 / 자리있음 {h['available']}건"
            if h.get("notified"):
                msg += f" / 🔔알림 {h['notified']}건"
            msg += "\n"
        return msg, False

    if cmd == "/date":
        if not args:
            return "사용법: /date 2026-09-26,2026-09-27", False
        cfg["sdate"] = args[0]
        return f"감시 날짜를 {args[0]} 로 변경했습니다.", True

    if cmd == "/seats":
        if not args or not args[0].isdigit():
            return "사용법: /seats 2", False
        cfg["min_seats"] = int(args[0])
        return f"최소 인원을 {args[0]}명으로 변경했습니다.", True

    if cmd == "/hours":
        if len(args) < 2 or not args[0].isdigit() or not args[1].isdigit():
            return "사용법: /hours 12 19  (12시~19시 출항)", False
        cfg["min_start_hour"] = int(args[0])
        cfg["max_start_hour"] = int(args[1])
        return f"출항 시간대를 {args[0]}시~{args[1]}시로 변경했습니다.", True

    if cmd == "/board":
        if not args or not args[0].isdigit():
            return "사용법: /board 10", False
        cfg["min_board_count"] = int(args[0])
        return f"최소 조황정보 건수를 {args[0]}건으로 변경했습니다.", True

    return None, False  # 모르는 명령은 무시


def process_commands(cfg):
    """텔레그램 명령을 읽어 처리한다. 설정이 바뀌었으면 True를 돌려준다."""
    changed = False
    for upd in tg_get_updates():
        msg = upd.get("message") or upd.get("edited_message")
        if not msg:
            continue
        text = (msg.get("text") or "").strip()
        if not text.startswith("/"):
            continue
        user_id = (msg.get("from") or {}).get("id")
        if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
            tg_send("이 봇의 명령을 사용할 권한이 없습니다.")
            continue
        reply, cfg_changed = handle_command(text, cfg)
        if reply:
            tg_send(reply)
        if cfg_changed:
            changed = True
    return changed


# ===== 메인 =====

def main():
    cfg = load_config()

    # 1) 먼저 텔레그램 명령 처리 (설정이 바뀔 수 있음)
    if process_commands(cfg):
        save_json(CONFIG_FILE, cfg)
        print("설정 변경됨:", cfg)

    # 2) 정기 모니터링
    try:
        listings = fetch_listings(cfg["sdate"])
    except Exception as e:
        print(f"선상24 조회 실패: {e}")
        return

    hits = [i for i in listings if matches(i, cfg)]
    available = [i for i in listings
                 if i.get("schedule_status_code") == "ING"
                 and i.get("remain_embarkation_num", 0) >= 1]

    seen = set(load_json(SEEN_FILE, []))
    new_items = [i for i in hits if str(i["schedule_no"]) not in seen]

    if new_items:
        msg = "🎣 조건에 맞는 새 낚시배가 나왔습니다!\n\n"
        msg += "\n".join(describe(i) for i in new_items)
        tg_send(msg)
        print(f"알림 전송: {len(new_items)}건")
    else:
        print("새 항목 없음")

    # 3) 상태 저장
    save_json(SEEN_FILE, sorted(seen | {str(i["schedule_no"]) for i in hits}))

    hist = load_json(HISTORY_FILE, [])
    hist.append({
        "time": now_kst(),
        "matched": len(hits),
        "available": len(available),
        "notified": len(new_items),
    })
    save_json(HISTORY_FILE, hist[-HISTORY_MAX:])


if __name__ == "__main__":
    main()
