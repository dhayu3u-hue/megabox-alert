
import os, sys, json, datetime
import requests

KST = datetime.timezone(datetime.timedelta(hours=9))
now = datetime.datetime.now(KST)

BRCH_NO = os.environ["BRCH_NO"]
KEYWORDS = [k.strip() for k in os.environ.get("KEYWORDS", "치이카와").split(",") if k.strip()]
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "21"))
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MANUAL = os.environ.get("EVENT_NAME") == "workflow_dispatch"

# 자동 실행은 수요일(2), 토요일(5)에만 (한국시간 기준)
if not MANUAL and now.weekday() not in (2, 5):
    print("수/토가 아니라 종료")
    sys.exit(0)

STATE_FILE = "state.json"
URL = "https://www.megabox.co.kr/on/oh/ohc/Brch/schedulePage.do"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
    "Referer": f"https://www.megabox.co.kr/theater?brchNo={BRCH_NO}",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
}
WD = "월화수목금토일"


def tg(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text},
        timeout=20,
    )
    r.raise_for_status()


def fetch(date_str):
    data = {
        "masterType": "brch",
        "detailType": "area",
        "brchNo": BRCH_NO,
        "firstAt": "N",
        "brchNo1": BRCH_NO,
        "crtDe": date_str,
        "playDe": date_str,
    }
    r = requests.post(URL, data=data, headers=HEADERS, timeout=20)
    r.raise_for_status()
    j = r.json()
    return j.get("megaMap", {}).get("movieFormList", []) or []


try:
    with open(STATE_FILE, encoding="utf-8") as f:
        seen = set(json.load(f))
except Exception:
    seen = set()

today = now.date()
new_by_date = {}
total_movies = set()
match_count = 0
errors = 0
all_seen = set(k for k in seen if k.split("|")[0] >= today.strftime("%Y%m%d"))

for i in range(DAYS_AHEAD + 1):
    d = today + datetime.timedelta(days=i)
    ds = d.strftime("%Y%m%d")
    try:
        items = fetch(ds)
    except Exception as e:
        errors += 1
        print("조회 실패", ds, repr(e))
        continue
    for it in items:
        name = it.get("movieNm", "")
        total_movies.add(name)
        if not any(k in name for k in KEYWORDS):
            continue
        match_count += 1
        key = f"{ds}|{it.get('playSchdlNo') or (name + str(it.get('playStartTime')))}"
        all_seen.add(key)
        if key not in seen:
            new_by_date.setdefault(ds, []).append(it)

if errors == DAYS_AHEAD + 1:
    print("전부 조회 실패 (사이트 차단 또는 구조 변경 가능성)")
    sys.exit(1)

for ds in sorted(new_by_date):
    d = datetime.datetime.strptime(ds, "%Y%m%d")
    lines = ["🎬 금정AK플라자 예매 오픈!", f"{d.month}/{d.day}({WD[d.weekday()]})"]
    by_movie = {}
    for it in new_by_date[ds]:
        by_movie.setdefault(it.get("movieNm", ""), []).append(it)
    for name, its in by_movie.items():
        lines.append(f"\n[{name}]")
        for it in sorted(its, key=lambda x: str(x.get("playStartTime"))):
            st = str(it.get("playStartTime", ""))
            theab = it.get("theabEngNm") or it.get("theabNm") or ""
            lines.append(f"- {st} {theab}".strip())
    lines.append("\n예매: https://www.megabox.co.kr/booking")
    tg("\n".join(lines))

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(sorted(all_seen), f, ensure_ascii=False, indent=0)

if MANUAL:
    tg(
        f"✅ 알리미 정상 작동 확인\n조회한 날짜: {DAYS_AHEAD + 1}일\n"
        f"전체 영화 {len(total_movies)}개 확인\n'{', '.join(KEYWORDS)}' 상영 회차: {match_count}건\n"
        f"조회 실패: {errors}건"
    )
print("완료", len(new_by_date), "개 날짜 알림")
