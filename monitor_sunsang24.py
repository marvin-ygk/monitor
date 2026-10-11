#!/usr/bin/env python3
"""
낚시배 빈자리 모니터링 봇 v2 (다인 사용)

- 감시 사이트: 선상24, 더피싱, 클럽씨피싱(남당항)
- 사용자: 관리자 승인을 받은 최대 10명, 1인당 감시 3개
- 상태(사용자·감시 목록 등)는 암호화해서 state.enc 하나로 저장
- cron-job.org가 10분마다 GitHub Actions를 실행시키는 구조를 전제로 함

필요한 GitHub Secrets
  TELEGRAM_BOT_TOKEN : 봇 토큰
  ADMIN_USER_ID      : 관리자(본인) 텔레그램 사용자 ID (숫자)
  STATE_KEY          : 상태 파일 암호화 비밀번호 (32자 이상 무작위 문자열 권장)

※ 공개 저장소에서는 Actions 실행 로그도 누구나 볼 수 있으므로,
  이 스크립트는 로그에 이름·ID·감시 조건 같은 개인정보를 출력하지 않는다.
"""
import base64
import hashlib
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ───────────────────────── 기본 설정 ─────────────────────────

KST = timezone(timedelta(hours=9))
BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
ADMIN_ID = int(os.environ.get("ADMIN_USER_ID", "0") or 0)
STATE_KEY = os.environ.get("STATE_KEY", "")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(BASE_DIR, "state.enc")
LEGACY_CONFIG = os.path.join(BASE_DIR, "config.json")   # v1 설정 (있으면 1회 이관)

MAX_USERS = 10            # 관리자 포함 최대 사용자 수
MAX_WATCHES = 3           # 1인당 감시 개수
MAX_DATES = 7             # 감시 1개당 날짜 수
MAX_PENDING = 5           # 승인 대기 최대 인원 (스팸 방지)
HEALTH_ALERT_AFTER = 3    # 연속 몇 회 이상 이상하면 관리자에게 경고
HISTORY_MAX = 50
TIME_BUDGET = 120         # 초. 이 시간이 지나면 새 조회를 멈춤 (실행 밀림 방지)
DEFAULT_BOARD = 10        # 선상24 최소 조황정보 건수 기본값

UA = {"User-Agent": "Mozilla/5.0"}
START = time.time()

SS_LIST = "https://api.sunsang24.com/ship/list"
SS_AREA = "https://api.sunsang24.com/ship/filter_area/general"
TF_LIST = "https://thefishing.kr/reservation/list.php"
CS_URL = "https://www.clubsea.co.kr/reservation?price_search=&search_date={ym}&day={dd}"

# ───────────────────────── 지역·어종 표 ─────────────────────────
# (2026-10 기준, 각 사이트 검색 화면에서 확인한 값)

SIDO_ALIASES = {
    "인천": "인천", "인천시": "인천", "인천광역시": "인천",
    "경기": "경기", "경기도": "경기",
    "충남": "충남", "충청남도": "충남",
    "전북": "전북", "전라북도": "전북",
    "전남": "전남", "전라남도": "전남",
    "강원": "강원", "강원도": "강원",
    "경북": "경북", "경상북도": "경북",
    "경남": "경남", "경상남도": "경남",
    "부산": "부산", "부산시": "부산", "부산광역시": "부산",
    "제주": "제주", "제주도": "제주",
}

# 더피싱 지역 코드 (sa[])
TF_SIDO = {
    "경기": ["4"], "인천": ["25"], "충남": ["5"], "전북": ["6"],
    "전남": ["60", "29"], "경남": ["101"], "부산": ["102"],
    "제주": ["130"], "강원": ["82"], "경북": ["83"],
}
TF_SUB = {
    "영흥도": ["27"], "화성": ["26"], "평택": ["66"],
    "당진": ["49"], "서산": ["45"], "홍성": ["54"], "태안": ["13"],
    "안면도": ["12"], "보령": ["38"], "서천": ["20"],
    "군산": ["7"], "격포": ["100"], "부안": ["100"], "신안": ["99"], "무안": ["99"],
    "여수": ["34"], "광양": ["34"], "목포": ["41"], "고흥": ["57"], "녹동": ["57"],
    "진도": ["63"], "해남": ["63"], "장흥": ["73"], "완도": ["73"], "강진": ["147"],
    "진해": ["103"], "마산": ["104"], "창원": ["103", "104"], "거제": ["105"],
    "통영": ["106"], "고성": ["107", "84"], "사천": ["108"], "남해": ["109"],
    "서귀포": ["131"], "제주시": ["132"],
    "속초": ["84"], "강릉": ["84"], "동해": ["85"], "삼척": ["85"],
    "울진": ["86"], "영덕": ["87"], "울산": ["89"], "경주": ["90"], "포항": ["112"],
}

# 어종: 표준 이름 -> (선상24에 보낼 이름, 더피싱 si[] 코드들, 클럽씨피싱 공지에서 찾을 단어들)
FISH = {
    "주꾸미": ("주꾸미", ["3", "36"], ["주꾸미", "쭈꾸미", "쭈갑"]),
    "갑오징어": ("갑오징어", ["6", "36"], ["갑오징어", "쭈갑"]),
    "광어": ("광어", ["5", "35"], ["광어"]),
    "우럭": ("우럭", ["4"], ["우럭"]),
    "참돔": ("참돔", ["1"], ["참돔"]),
    "갈치": ("갈치", ["2"], ["갈치"]),
    "농어": ("농어", ["21"], ["농어"]),
    "문어": ("문어", ["7"], ["문어"]),
    "백조기": ("백조기", ["8"], ["조기"]),
    "대구": ("대구", ["9"], ["대구"]),
    "열기": ("열기", ["10"], ["열기"]),
    "볼락": ("볼락", ["11"], ["볼락", "뽈락"]),
    "가자미": ("참가자미", ["12"], ["가자미"]),
    "도다리": ("도다리", ["13"], ["도다리"]),
    "노래미": ("노래미", ["14"], ["노래미"]),
    "민어": ("민어", ["20"], ["민어"]),
    "감성돔": ("감성돔", ["22"], ["감성돔"]),
    "오징어": ("오징어", ["23"], ["오징어"]),
    "한치": ("한치", ["24"], ["한치"]),
    "부시리": ("부시리", ["27"], ["부시리"]),
    "고등어": ("고등어", ["28"], ["고등어"]),
    "삼치": ("삼치", ["48"], ["삼치"]),
    "낙지": ("낙지", ["53"], ["낙지"]),
    "꽃게": ("꽃게", ["56"], ["꽃게"]),
    "방어": ("방어", ["54"], ["방어"]),
}
FISH_ALIASES = {"쭈꾸미": "주꾸미", "쭈꾸": "주꾸미", "쭈": "주꾸미", "갑오": "갑오징어",
                "뽈락": "볼락", "참가자미": "가자미", "조기": "백조기"}

# 클럽씨피싱(남당항, 충남 홍성)을 함께 조회할 지역 이름
CS_REGIONS = {"충남", "홍성", "남당", "남당항"}


# ───────────────────────── 공통 도구 ─────────────────────────

def log(msg):
    """공개 로그용. 개인정보는 절대 넣지 않는다."""
    print(msg, flush=True)


def now_kst():
    return datetime.now(KST)


def today_str():
    return now_kst().strftime("%Y-%m-%d")


def out_of_time():
    return time.time() - START > TIME_BUDGET


def http_get(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def strip_tags(t):
    t = re.sub(r"<[^>]+>", " ", t)
    for a, b in (("&gt;", ">"), ("&lt;", "<"), ("&amp;", "&"), ("&nbsp;", " "), ("&quot;", '"')):
        t = t.replace(a, b)
    return " ".join(t.split())


def norm_place(name):
    """'보령시' -> '보령', '서산시' -> '서산' (두 글자 이름은 그대로)"""
    n = name.strip().replace(" ", "")
    if len(n) >= 3 and n[-1] in "시군" and n not in ("제주시",):
        n = n[:-1]
    return n


# ───────────────────────── 암호화 상태 저장 ─────────────────────────

def _fernet(salt):
    from cryptography.fernet import Fernet
    raw = hashlib.pbkdf2_hmac("sha256", STATE_KEY.encode(), salt, 200_000)
    return Fernet(base64.urlsafe_b64encode(raw))


def new_state():
    return {
        "v": 2,
        "users": {},        # uid(str) -> 사용자 정보
        "offset": 0,        # 텔레그램 메시지 읽은 위치
        "paused_all": False,
        "health": {},       # 사이트 -> {"bad": 연속 이상 횟수, "alerted": bool}
        "history": [],
        "ss_area_cache": None,
    }


def load_state():
    if not STATE_KEY:
        log("STATE_KEY 가 없습니다. 암호화 없이 저장하지 않도록 중단합니다.")
        sys.exit(1)
    if not os.path.exists(STATE_FILE):
        st = new_state()
        migrate_legacy(st)
        return st
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        box = json.load(f)
    try:
        data = _fernet(base64.b64decode(box["salt"])).decrypt(box["data"].encode())
    except Exception:
        # 키가 틀리면 기존 상태를 덮어쓰지 않도록 반드시 중단한다
        log("상태 파일 복호화 실패: STATE_KEY 가 바뀌었거나 파일이 손상되었습니다.")
        tg_send(ADMIN_ID, "⚠️ 상태 파일 복호화 실패. STATE_KEY 를 확인하세요. (상태는 보존됨)")
        sys.exit(1)
    st = json.loads(data.decode("utf-8"))
    for k, v in new_state().items():
        st.setdefault(k, v)
    return st


def save_state(st):
    salt = os.urandom(16)
    token = _fernet(salt).encrypt(json.dumps(st, ensure_ascii=False).encode("utf-8"))
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"v": 2, "salt": base64.b64encode(salt).decode(),
                   "data": token.decode()}, f)
    os.replace(tmp, STATE_FILE)


def make_user(uid, name, status):
    return {"id": int(uid), "name": name, "status": status,
            "joined": today_str(), "paused": False,
            "min_hour": 0, "max_hour": 24, "board": DEFAULT_BOARD,
            "watches": [], "next_wid": 1}


def migrate_legacy(st):
    """v1 의 config.json 이 있으면 관리자 감시 1개로 옮겨준다 (1회)."""
    if not ADMIN_ID:
        return
    admin = make_user(ADMIN_ID, "관리자", "active")
    if os.path.exists(LEGACY_CONFIG):
        try:
            with open(LEGACY_CONFIG, encoding="utf-8") as f:
                old = json.load(f)
            dates = [d.strip() for d in str(old.get("sdate", "")).split(",") if d.strip()]
            admin["min_hour"] = int(old.get("min_start_hour", 0))
            admin["max_hour"] = int(old.get("max_start_hour", 24))
            admin["board"] = int(old.get("min_board_count", DEFAULT_BOARD))
            if dates:
                admin["watches"].append({
                    "wid": 1, "dates": dates[:MAX_DATES], "fish": "주꾸미",
                    "regions": ["인천", "경기", "충남"],
                    "seats": int(old.get("min_seats", 2)), "open": {}})
                admin["next_wid"] = 2
            log("v1 설정 이관 완료")
        except Exception as e:
            log(f"v1 설정 이관 실패: {type(e).__name__}")
    st["users"][str(ADMIN_ID)] = admin


# ───────────────────────── 텔레그램 ─────────────────────────

def tg_call(method, params):
    if not BOT_TOKEN:
        return None
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=20) as r:
            return json.load(r)
    except Exception as e:
        log(f"텔레그램 {method} 실패: {type(e).__name__}")
        return None


def tg_send(chat_id, text):
    if not chat_id:
        return
    # 텔레그램 메시지 최대 4096자 → 나눠서 보냄
    chunks, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > 3800:
            chunks.append(buf)
            buf = ""
        buf += line + "\n"
    if buf.strip():
        chunks.append(buf)
    for c in chunks:
        tg_call("sendMessage", {"chat_id": chat_id, "text": c,
                                "disable_web_page_preview": "true"})
        time.sleep(0.05)


def tg_updates(st):
    params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if st["offset"]:
        params["offset"] = st["offset"]
    res = tg_call("getUpdates", params)
    if not res or not res.get("ok"):
        return []
    ups = res.get("result", [])
    if ups:
        st["offset"] = ups[-1]["update_id"] + 1
    return ups


# ───────────────────────── 사이트 조회 (실행 중 캐시) ─────────────────────────

class Fetcher:
    """같은 조건을 여러 사람이 감시해도 사이트에는 한 번만 요청한다."""

    def __init__(self, st):
        self.st = st
        self.cache = {}
        self.fail = {"선상24": 0, "더피싱": 0, "클럽씨피싱": 0}
        self.ok = {"선상24": 0, "더피싱": 0, "클럽씨피싱": 0}
        self.calls = 0
        self.ss_areas = None

    # ── 선상24 지역표 (사이트에서 직접 받아옴) ──
    def ss_area_table(self):
        if self.ss_areas is not None:
            return self.ss_areas
        table = None
        try:
            d = json.loads(http_get(SS_AREA))
            table = {"sido": {}, "sub": {}}
            for grp in d.get("area", []):
                title = grp.get("title", "")
                subs = [str(i["no"]) for i in grp.get("items", []) if i.get("no", 0) > 0]
                table["sido"][title] = subs
                for i in grp.get("items", []):
                    if i.get("no", 0) > 0:
                        table["sub"].setdefault(norm_place(i["name"]), []).append(str(i["no"]))
            self.st["ss_area_cache"] = table
        except Exception as e:
            log(f"선상24 지역표 조회 실패: {type(e).__name__} (이전 표 사용)")
            table = self.st.get("ss_area_cache") or {"sido": {}, "sub": {}}
        self.ss_areas = table
        return table

    def ss_codes(self, regions):
        t = self.ss_area_table()
        codes = []
        for r in regions:
            codes += t["sido"].get(r, []) or t["sub"].get(norm_place(r), [])
        return sorted(set(codes), key=int)

    # ── 선상24 ──
    def sunsang24(self, fish, regions, dates):
        key = ("SS", fish, tuple(sorted(regions)), tuple(sorted(dates)))
        if key in self.cache:
            return self.cache[key]
        codes = self.ss_codes(regions)
        if not codes or out_of_time():
            self.cache[key] = None
            return None
        items = []
        try:
            for page in range(1, 6):
                params = {"area": ",".join(codes), "area_text": "", "area_type": "area",
                          "fish": FISH[fish][0], "is_possible": "is_possible",
                          "page": page, "sdate": ",".join(sorted(dates)), "type": "general"}
                self.calls += 1
                d = json.loads(http_get(SS_LIST + "?" + urllib.parse.urlencode(params)))
                lst = d["list"]
                avail = [i for i in lst if i.get("schedule_status_code") == "ING"
                         and i.get("remain_embarkation_num", 0) > 0]
                items += avail
                # 예약 가능(ING) 일정이 앞쪽, 마감(END)이 뒤쪽에 정렬되므로
                # 페이지 마지막 항목이 마감이면 더 볼 필요가 없다
                if not lst or lst[-1].get("schedule_status_code") != "ING":
                    break
            self.ok["선상24"] += 1
        except Exception as e:
            log(f"선상24 조회 실패: {type(e).__name__}")
            self.fail["선상24"] += 1
            self.cache[key] = None
            return None
        result = []
        for i in items:
            result.append({
                "site": "선상24",
                "key": f"S|{i['schedule_no']}",
                "date": i.get("sdate", ""),
                "name": i["ship"]["name"],
                "port": i.get("port_name", ""),
                "time": f"{(i.get('stime') or '')[:5]}~{(i.get('etime') or '')[:5]}",
                "hour": _hour(i.get("stime")),
                "seats": i.get("remain_embarkation_num", 0),
                "board": i.get("board_write_num", 0),
                "price": i.get("price", 0),
                "url": ("https://www.sunsang24.com/mypage/reservation_ready/"
                        f"{i['ship'].get('no', '')}/{i.get('schedule_no', '')}/{i.get('sdate', '')}/"),
            })
        self.cache[key] = result
        return result

    # ── 더피싱 ──
    def tf_codes(self, regions):
        codes = []
        for r in regions:
            codes += TF_SIDO.get(r, []) or TF_SUB.get(norm_place(r), [])
        return sorted(set(codes), key=int)

    def tf_url(self, fish, regions, date, page=1):
        q = [("sf[]", "1")]
        q += [("si[]", c) for c in FISH[fish][1]]
        q += [("sa[]", c) for c in self.tf_codes(regions)]
        q += [("search_date", date)]
        if page > 1:
            q += [("page", str(page))]
        return TF_LIST + "?" + urllib.parse.urlencode(q)

    def thefishing(self, fish, regions, date):
        key = ("TF", fish, tuple(sorted(regions)), date)
        if key in self.cache:
            return self.cache[key]
        if not self.tf_codes(regions) or out_of_time():
            self.cache[key] = None
            return None
        result = []
        try:
            for page in range(1, 6):
                self.calls += 1
                html = http_get(self.tf_url(fish, regions, date, page))
                idx = html.find("re_list re_list_etc")
                if idx == -1:
                    if page == 1 and "re_list" not in html:
                        raise ValueError("화면 구조 변경 의심")
                    break
                n = 0
                for block in html[idx:].split("<li")[1:]:
                    m = re.search(r'class="re_list_ship"[^>]*>(.*?)</div>', block, re.S)
                    if not m:
                        continue
                    n += 1
                    area = re.search(r'class="re_list_area"[^>]*>(.*?)</div>', block, re.S)
                    fsh = re.search(r'class="re_list_fish"[^>]*>(.*?)</div>', block, re.S)
                    name = strip_tags(m.group(1))
                    result.append({
                        "site": "더피싱", "key": f"T|{date}|{name}", "date": date,
                        "name": name, "port": strip_tags(area.group(1)) if area else "",
                        "fish": strip_tags(fsh.group(1)) if fsh else "",
                        "seats": None, "url": self.tf_url(fish, regions, date),
                    })
                if n < 20:      # 한 페이지 20개. 덜 차면 마지막 페이지
                    break
            self.ok["더피싱"] += 1
        except Exception as e:
            log(f"더피싱 조회 실패: {type(e).__name__}")
            self.fail["더피싱"] += 1
            self.cache[key] = None
            return None
        self.cache[key] = result
        return result

    # ── 클럽씨피싱 (한 번 받으면 그 달 전체) ──
    def clubsea(self, date):
        ym, dd = date[:7], date[8:10]
        key = ("CS", ym)
        if key not in self.cache:
            if out_of_time():
                return None
            try:
                self.calls += 1
                html = http_get(CS_URL.format(ym=ym, dd=dd), timeout=25)
                if "reservation" not in html:
                    raise ValueError("화면 구조 변경 의심")
                self.cache[key] = html
                self.ok["클럽씨피싱"] += 1
            except Exception as e:
                log(f"클럽씨피싱 조회 실패: {type(e).__name__}")
                self.fail["클럽씨피싱"] += 1
                self.cache[key] = None
        html = self.cache[key]
        if html is None:
            return None
        result = []
        for row in html.split("<tr")[1:]:
            seat_m = re.search(r'class="reservation_count[^"]*"[^>]*>(.*?)</span>', row, re.S)
            if not seat_m:
                continue
            dm = re.search(r"20\d\d-\d{2}-\d{2}", row)
            if not dm or dm.group(0) != date:
                continue
            num = re.match(r"(\d+)명", strip_tags(seat_m.group(1)))
            if not num:
                continue     # '예약완료' 등
            name_m = (re.search(r"<td[^>]*>((?:(?!<td)[\s\S])*?)<button", row)
                      or re.search(r"<td[^>]*>((?:(?!<td)[\s\S])*?)<a\b", row))
            fish_m = re.search(r"공지\s*</span>\s*([^<]{1,60})", row)
            name = strip_tags(name_m.group(1)) if name_m else "(이름 확인 불가)"
            # ※ 예약자 실명은 수집하지 않는다 (배 이름·공지·잔여석만)
            result.append({
                "site": "클럽씨피싱", "key": f"C|{date}|{name}", "date": date,
                "name": name, "port": "홍성 남당항",
                "fish": strip_tags(fish_m.group(1)) if fish_m else "",
                "seats": int(num.group(1)), "url": CS_URL.format(ym=ym, dd=dd),
            })
        return result


def _hour(stime):
    try:
        return int((stime or "99").split(":")[0])
    except ValueError:
        return 99


# ───────────────────────── 감시 평가 ─────────────────────────

def watch_matches(f, user, w):
    """감시 1개에 대해 현재 조건에 맞는 항목들과, 조회 실패한 사이트 목록을 돌려준다."""
    found, failed = [], set()
    fish, regions, dates, seats = w["fish"], w["regions"], w["dates"], w["seats"]

    ss = f.sunsang24(fish, regions, dates)
    if ss is None:
        if f.ss_codes(regions):
            failed.add("선상24")
    else:
        for i in ss:
            if (i["date"] in dates and i["seats"] >= seats
                    and user["min_hour"] <= i["hour"] < user["max_hour"]
                    and i["board"] >= user["board"]):
                found.append(i)

    for d in dates:
        tf = f.thefishing(fish, regions, d)
        if tf is None:
            if f.tf_codes(regions):
                failed.add("더피싱")
        else:
            found += tf      # 잔여석 미공개 → 인원 조건 적용 불가

    if any(norm_place(r) in CS_REGIONS for r in regions):
        words = FISH[fish][2]
        for d in dates:
            cs = f.clubsea(d)
            if cs is None:
                failed.add("클럽씨피싱")
                continue
            for i in cs:
                if i["seats"] >= seats and any(wd in i["fish"] for wd in words):
                    found.append(i)

    uniq = {}
    for i in found:
        uniq.setdefault(i["key"], i)
    return list(uniq.values()), failed


def describe(i):
    if i["site"] == "선상24":
        info = (f"{i['date']} {i['time']} / 남은자리 {i['seats']}명 / "
                f"조황 {i['board']}건 / 인당 {i['price']:,}원")
    elif i["site"] == "더피싱":
        info = f"{i['date']} / {i.get('fish', '')} / 잔여석 미공개"
    else:
        info = f"{i['date']} / {i.get('fish', '')} / 남은자리 {i['seats']}명"
    return f"- [{i['site']}] {i['name']} ({i['port']})\n  {info}\n  {i['url']}"


def watch_label(w):
    return (f"#{w['wid']} {','.join(d[5:] for d in w['dates'])} "
            f"{w['fish']} {','.join(w['regions'])} {w['seats']}명")


def run_monitor(st, f):
    """활성 사용자별로 '자리 없음 → 있음' 으로 바뀐 항목만 알린다 (취소 자리 재알림 포함)."""
    sent = 0
    for uid, u in st["users"].items():
        if u["status"] != "active" or u["paused"]:
            continue
        for w in u["watches"]:
            if out_of_time():
                log("시간 초과: 나머지 감시는 다음 실행에서 처리")
                return sent
            found, failed = watch_matches(f, u, w)
            prev = w.get("open", {})
            now_open = {i["key"]: i["site"] for i in found}
            # 조회에 실패한 사이트는 이전 상태를 유지 (복구 시 가짜 재알림 방지)
            for k, site in prev.items():
                if site in failed:
                    now_open[k] = site
            new = [i for i in found if i["key"] not in prev]
            w["open"] = now_open
            if new:
                msg = f"🎣 새 빈자리 [{watch_label(w)}]\n\n" + "\n".join(describe(i) for i in new)
                tg_send(int(uid), msg)
                sent += len(new)
    return sent


# ───────────────────────── 정리·이상 감지 ─────────────────────────

def cleanup_past(st):
    today = today_str()
    for uid, u in st["users"].items():
        keep = []
        for w in u["watches"]:
            w["dates"] = [d for d in w["dates"] if d >= today]
            if w["dates"]:
                keep.append(w)
            else:
                tg_send(int(uid), f"📅 날짜가 모두 지나 감시 #{w['wid']} 를 자동 삭제했습니다.")
        u["watches"] = keep


def check_health(st, f):
    for site in ("선상24", "더피싱", "클럽씨피싱"):
        h = st["health"].setdefault(site, {"bad": 0, "alerted": False})
        if f.fail[site] and not f.ok[site]:
            h["bad"] += 1
        elif f.ok[site]:
            if h["alerted"]:
                tg_send(ADMIN_ID, f"✅ {site} 조회가 정상으로 돌아왔습니다.")
            h["bad"], h["alerted"] = 0, False
        if h["bad"] >= HEALTH_ALERT_AFTER and not h["alerted"]:
            tg_send(ADMIN_ID, f"⚠️ {site} 조회가 {h['bad']}회 연속 실패했습니다. "
                              "사이트 개편이나 접속 차단 여부를 확인하세요.")
            h["alerted"] = True


# ───────────────────────── 명령 처리 ─────────────────────────

HELP_USER = (
    "🎣 사용법\n\n"
    "/add 날짜 어종 지역 인원 — 감시 추가 (최대 3개)\n"
    "  예) /add 2026-10-03 주꾸미 충남,경기 2\n"
    "  날짜 여러 개: 2026-10-03,2026-10-04\n"
    "  기간: 2026-10-03~2026-10-05 (최대 7일)\n"
    "/list — 내 감시 목록\n"
    "/del 번호 — 감시 삭제\n"
    "/status — 내 감시 조건으로 지금 바로 조회\n"
    "/pause, /resume — 내 알림 일시정지/재개\n"
    "/hours 12 19 — 선상24 출항시간 범위 (기본 0~24)\n"
    "/board 10 — 선상24 최소 조황정보 건수 (기본 10)\n"
    "/fish — 지원 어종, /regions — 지원 지역\n"
    "/history — 최근 실행 기록, /ping — 동작 확인\n"
    "/leave — 탈퇴\n\n"
    "※ 10분마다 확인하므로 응답까지 최대 10분 걸립니다.\n"
    "※ 더피싱은 잔여석을 공개하지 않아 인원 조건이 적용되지 않습니다."
)
HELP_ADMIN = (
    "\n\n👑 관리자\n"
    "/users — 사용자 목록\n"
    "/approve ID, /reject ID — 가입 승인/거절\n"
    "/kick ID — 사용자 삭제\n"
    "/pauseall, /resumeall — 전체 조회 정지/재개 (명령 처리는 계속)\n"
    "/health — 사이트 상태\n"
    "/notice 내용 — 전체 공지"
)


def active_count(st):
    return sum(1 for u in st["users"].values() if u["status"] == "active")


def parse_dates(s):
    s = s.strip()
    out = []
    if "~" in s:
        a, b = s.split("~", 1)
        d0 = datetime.strptime(a.strip(), "%Y-%m-%d")
        d1 = datetime.strptime(b.strip(), "%Y-%m-%d")
        if d1 < d0 or (d1 - d0).days >= MAX_DATES:
            raise ValueError(f"기간은 최대 {MAX_DATES}일까지 가능합니다.")
        while d0 <= d1:
            out.append(d0.strftime("%Y-%m-%d"))
            d0 += timedelta(days=1)
    else:
        for x in s.split(","):
            out.append(datetime.strptime(x.strip(), "%Y-%m-%d").strftime("%Y-%m-%d"))
    out = sorted(set(out))
    if len(out) > MAX_DATES:
        raise ValueError(f"날짜는 최대 {MAX_DATES}개까지 가능합니다.")
    if any(d < today_str() for d in out):
        raise ValueError("지난 날짜는 넣을 수 없습니다.")
    return out


def parse_fish(s):
    s = s.strip()
    s = FISH_ALIASES.get(s, s)
    if s not in FISH:
        raise ValueError(f"지원하지 않는 어종입니다: {s}\n/fish 로 목록을 확인하세요.")
    return s


def parse_regions(s, f):
    out = []
    for r in s.split(","):
        r = r.strip()
        if not r:
            continue
        r = SIDO_ALIASES.get(r, r)
        known = (r in TF_SIDO or norm_place(r) in TF_SUB
                 or f.ss_codes([r]) or norm_place(r) in CS_REGIONS)
        if not known:
            raise ValueError(f"알 수 없는 지역입니다: {r}\n/regions 로 목록을 확인하세요.")
        out.append(r)
    if not out:
        raise ValueError("지역을 하나 이상 넣어주세요.")
    return sorted(set(out))


def handle(st, f, msg):
    chat = msg.get("chat", {})
    if chat.get("type") != "private":
        return      # 그룹방 메시지는 무시 (각자 1:1 대화로 사용)
    frm = msg.get("from", {})
    uid = int(frm.get("id", 0))
    text = (msg.get("text") or "").strip()
    if not uid or not text.startswith("/"):
        return
    parts = text.split(maxsplit=1)
    head = parts[0].split("@")[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""
    # '/approve_123' 처럼 눌러서 보내는 형태도 지원
    m = re.match(r"^(/[a-z]+)_(\d+)$", head)
    if m:
        head, arg = m.group(1), m.group(2)

    is_admin = uid == ADMIN_ID
    u = st["users"].get(str(uid))
    name = (" ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x)
            or frm.get("username") or "이름없음")

    # 관리자는 항상 등록 상태로 보장
    if is_admin and (not u or u["status"] != "active"):
        u = st["users"].setdefault(str(uid), make_user(uid, name, "active"))
        u["status"] = "active"

    # ── 미가입자 ──
    if not u or u["status"] != "active":
        if head == "/join":
            if u and u["status"] == "pending":
                tg_send(uid, "이미 가입 신청 중입니다. 관리자 승인을 기다려 주세요.")
                return
            if active_count(st) >= MAX_USERS:
                tg_send(uid, f"정원({MAX_USERS}명)이 가득 차 신청할 수 없습니다.")
                return
            pending = sum(1 for x in st["users"].values() if x["status"] == "pending")
            if pending >= MAX_PENDING:
                tg_send(uid, "현재 승인 대기자가 많아 신청할 수 없습니다. 나중에 다시 시도해 주세요.")
                return
            st["users"][str(uid)] = make_user(uid, name, "pending")
            tg_send(uid, "가입 신청을 보냈습니다. 관리자가 승인하면 알려드립니다.")
            tg_send(ADMIN_ID, f"🙋 가입 신청: {name} (ID {uid})\n"
                              f"승인: /approve_{uid}\n거절: /reject_{uid}")
        else:
            tg_send(uid, "🎣 낚시배 빈자리 알림봇입니다.\n이용하려면 /join 으로 가입 신청해 주세요.")
        return

    u["name"] = name

    # ── 관리자 전용 ──
    if head in ("/approve", "/reject", "/kick", "/users", "/pauseall",
                "/resumeall", "/health", "/notice"):
        if not is_admin:
            tg_send(uid, "관리자 전용 명령입니다.")
            return
        if head == "/users":
            lines = [f"👥 사용자 {active_count(st)}/{MAX_USERS}명"]
            for x in st["users"].values():
                tag = {"active": "", "pending": " [승인대기]"}.get(x["status"], "")
                ps = " [정지]" if x.get("paused") else ""
                lines.append(f"- {x['name']} (ID {x['id']}){tag}{ps} 감시 {len(x['watches'])}개")
            tg_send(uid, "\n".join(lines))
        elif head == "/health":
            lines = ["🩺 사이트 상태"]
            for s in ("선상24", "더피싱", "클럽씨피싱"):
                h = st["health"].get(s, {"bad": 0})
                lines.append(f"- {s}: {'정상' if h['bad'] == 0 else str(h['bad']) + '회 연속 실패'}")
            lines.append(f"전체 조회: {'정지' if st['paused_all'] else '동작 중'}")
            tg_send(uid, "\n".join(lines))
        elif head == "/pauseall":
            st["paused_all"] = True
            tg_send(uid, "⏸ 전체 사이트 조회를 정지했습니다. 명령 처리는 계속됩니다.")
        elif head == "/resumeall":
            st["paused_all"] = False
            for x in st["users"].values():
                for w in x["watches"]:
                    w["open"] = {}      # 재개 시 현재 빈자리를 다시 알려줌
            tg_send(uid, "▶️ 전체 조회를 재개했습니다.")
        elif head == "/notice":
            if not arg:
                tg_send(uid, "사용법: /notice 내용")
                return
            for x in st["users"].values():
                if x["status"] == "active":
                    tg_send(x["id"], f"📢 공지\n{arg}")
        else:
            if not arg.isdigit():
                tg_send(uid, f"사용법: {head} 사용자ID")
                return
            t = st["users"].get(arg)
            if not t:
                tg_send(uid, "해당 사용자가 없습니다.")
                return
            if head == "/approve":
                if t["status"] == "active":
                    tg_send(uid, "이미 승인된 사용자입니다.")
                elif active_count(st) >= MAX_USERS:
                    tg_send(uid, f"정원({MAX_USERS}명)이 가득 찼습니다.")
                else:
                    t["status"] = "active"
                    tg_send(uid, f"승인 완료: {t['name']}")
                    tg_send(t["id"], "✅ 가입이 승인되었습니다.\n\n" + HELP_USER)
            elif head == "/reject":
                if t["status"] != "pending":
                    tg_send(uid, "승인 대기 중인 사용자가 아닙니다.")
                else:
                    del st["users"][arg]
                    tg_send(uid, "거절했습니다.")
                    tg_send(t["id"], "가입 신청이 거절되었습니다.")
            elif head == "/kick":
                if int(arg) == ADMIN_ID:
                    tg_send(uid, "관리자는 삭제할 수 없습니다.")
                else:
                    del st["users"][arg]
                    tg_send(uid, f"삭제했습니다: {t['name']}")
                    tg_send(t["id"], "이용이 종료되었습니다.")
        return

    # ── 일반 사용자 ──
    if head in ("/start", "/help"):
        tg_send(uid, HELP_USER + (HELP_ADMIN if is_admin else ""))

    elif head == "/add":
        if len(u["watches"]) >= MAX_WATCHES:
            tg_send(uid, f"감시는 최대 {MAX_WATCHES}개까지입니다. /del 로 정리 후 추가하세요.")
            return
        p = arg.split()
        if len(p) != 4:
            tg_send(uid, "사용법: /add 날짜 어종 지역 인원\n예) /add 2026-10-03 주꾸미 충남,경기 2")
            return
        try:
            dates = parse_dates(p[0])
            fish = parse_fish(p[1])
            regions = parse_regions(p[2], f)
            if not p[3].isdigit() or not 1 <= int(p[3]) <= 30:
                raise ValueError("인원은 1~30 사이 숫자로 넣어주세요.")
        except ValueError as e:
            tg_send(uid, f"입력 오류: {e}")
            return
        w = {"wid": u["next_wid"], "dates": dates, "fish": fish,
             "regions": regions, "seats": int(p[3]), "open": {}}
        u["next_wid"] += 1
        u["watches"].append(w)
        note = ""
        if not f.ss_codes(regions):
            note += "\n※ 선상24에는 해당 지역이 없어 제외됩니다."
        if not f.tf_codes(regions):
            note += "\n※ 더피싱에는 해당 지역이 없어 제외됩니다."
        if any(norm_place(r) in CS_REGIONS for r in regions):
            note += "\n※ 클럽씨피싱(홍성 남당항)도 함께 조회합니다."
        tg_send(uid, f"➕ 감시 추가: {watch_label(w)}{note}\n다음 확인 때부터 빈자리를 알려드립니다.")

    elif head == "/list":
        if not u["watches"]:
            tg_send(uid, "감시 중인 조건이 없습니다. /add 로 추가하세요.")
            return
        lines = [f"📋 내 감시 ({len(u['watches'])}/{MAX_WATCHES})"]
        lines += [f"- {watch_label(w)} (현재 빈자리 {len(w.get('open', {}))}건)" for w in u["watches"]]
        lines.append(f"\n선상24 필터: 출항 {u['min_hour']}~{u['max_hour']}시, 조황 {u['board']}건 이상")
        lines.append("알림: " + ("⏸ 일시정지" if u["paused"] else "▶️ 동작 중"))
        tg_send(uid, "\n".join(lines))

    elif head == "/del":
        a = arg.lstrip("#")
        before = len(u["watches"])
        u["watches"] = [w for w in u["watches"] if str(w["wid"]) != a]
        tg_send(uid, f"감시 #{a} 를 삭제했습니다." if len(u["watches"]) < before
                else "해당 번호가 없습니다. /list 로 확인하세요.")

    elif head == "/status":
        if not u["watches"]:
            tg_send(uid, "감시 중인 조건이 없습니다.")
            return
        lines = ["🔎 지금 조회한 결과"]
        for w in u["watches"]:
            found, failed = watch_matches(f, u, w)
            lines.append(f"\n[{watch_label(w)}] {len(found)}건")
            lines += [describe(i) for i in found]
            if failed:
                lines.append(f"  (조회 실패: {', '.join(sorted(failed))})")
        if st["paused_all"]:
            lines.append("\n※ 현재 관리자가 전체 조회를 정지한 상태라 자동 알림은 오지 않습니다.")
        tg_send(uid, "\n".join(lines))

    elif head == "/pause":
        u["paused"] = True
        tg_send(uid, "⏸ 내 알림을 일시정지했습니다. /resume 으로 재개합니다.")

    elif head == "/resume":
        u["paused"] = False
        for w in u["watches"]:
            w["open"] = {}
        tg_send(uid, "▶️ 알림을 재개했습니다. 다음 확인 때 현재 빈자리를 알려드립니다.")

    elif head == "/hours":
        p = arg.split()
        if len(p) != 2 or not all(x.isdigit() for x in p) or not 0 <= int(p[0]) < int(p[1]) <= 24:
            tg_send(uid, "사용법: /hours 12 19 (0~24)")
            return
        u["min_hour"], u["max_hour"] = int(p[0]), int(p[1])
        tg_send(uid, f"선상24 출항시간 {p[0]}~{p[1]}시로 변경했습니다.")

    elif head == "/board":
        if not arg.isdigit():
            tg_send(uid, "사용법: /board 10")
            return
        u["board"] = int(arg)
        tg_send(uid, f"선상24 최소 조황정보 {arg}건으로 변경했습니다.")

    elif head == "/fish":
        tg_send(uid, "🐟 지원 어종\n" + ", ".join(FISH) +
                "\n\n※ 주꾸미·갑오징어는 더피싱의 '쭈갑' 출조도 함께 조회합니다.")

    elif head == "/regions":
        t = f.ss_area_table()
        lines = ["🗺 지원 지역 (시·도 또는 시·군 이름)",
                 "시·도: " + ", ".join(sorted(TF_SIDO))]
        subs = sorted(set(list(TF_SUB) + list(t.get("sub", {}))))
        lines.append("시·군: " + ", ".join(subs))
        lines.append("\n※ 선상24는 '경기'에 인천이 포함되지 않습니다. 필요하면 '인천,경기'로 넣으세요.")
        lines.append("※ '충남' 또는 '홍성'을 넣으면 클럽씨피싱(남당항)도 조회합니다.")
        tg_send(uid, "\n".join(lines))

    elif head == "/history":
        h = st["history"][-15:]
        if not h:
            tg_send(uid, "아직 실행 기록이 없습니다.")
            return
        lines = ["📊 최근 실행 기록"]
        for x in reversed(h):
            lines.append(f"{x['t']} 조회 {x['calls']}회 / 알림 {x['sent']}건"
                         + (f" / 실패 {x['fail']}" if x.get("fail") else "")
                         + (" / 정지중" if x.get("paused") else ""))
        tg_send(uid, "\n".join(lines))

    elif head == "/ping":
        last = st["history"][-1]["t"] if st["history"] else "기록 없음"
        tg_send(uid, f"✅ 동작 중\n현재: {now_kst():%m-%d %H:%M}\n직전 실행: {last}\n"
                     f"전체 조회: {'정지' if st['paused_all'] else '동작 중'}")

    elif head == "/leave":
        if is_admin:
            tg_send(uid, "관리자는 탈퇴할 수 없습니다.")
        else:
            del st["users"][str(uid)]
            tg_send(uid, "탈퇴했습니다. 다시 이용하려면 /join 하세요.")
            tg_send(ADMIN_ID, f"👋 {name} 님이 탈퇴했습니다.")

    else:
        tg_send(uid, "모르는 명령입니다. /help 를 확인하세요.")


# ───────────────────────── 메인 ─────────────────────────

def main():
    if not BOT_TOKEN or not ADMIN_ID:
        log("TELEGRAM_BOT_TOKEN 또는 ADMIN_USER_ID 가 없습니다.")
        sys.exit(1)
    st = load_state()
    f = Fetcher(st)

    # 1) 명령 처리 (전체 정지 중에도 항상 처리)
    n_cmd = 0
    for upd in tg_updates(st):
        msg = upd.get("message")
        if msg:
            try:
                handle(st, f, msg)
                n_cmd += 1
            except Exception as e:
                log(f"명령 처리 오류: {type(e).__name__}")

    # 2) 지난 날짜 정리
    cleanup_past(st)

    # 3) 사이트 조회 (전체 정지가 아닐 때만)
    sent = 0
    if not st["paused_all"]:
        sent = run_monitor(st, f)
        check_health(st, f)

    fails = ",".join(s for s, n in f.fail.items() if n)
    st["history"].append({"t": f"{now_kst():%m-%d %H:%M}", "calls": f.calls,
                          "sent": sent, "fail": fails, "paused": st["paused_all"]})
    st["history"] = st["history"][-HISTORY_MAX:]
    save_state(st)
    log(f"완료: 명령 {n_cmd}건, 조회 {f.calls}회, 알림 {sent}건, "
        f"실패 [{fails or '없음'}], {time.time() - START:.1f}초")


if __name__ == "__main__":
    main()
