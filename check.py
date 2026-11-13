import os, sys, json, time, datetime
import requests

KST = datetime.timezone(datetime.timedelta(hours=9))
now = datetime.datetime.now(KST)

BRCH_NO = os.environ["BRCH_NO"]
KEYWORDS = [k.strip() for k in os.environ.get("KEYWORDS", "치이카와").split(",") if k.strip()]
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MANUAL = os.environ.get("EVENT_NAME") == "workflow_dispatch"

MAX_AHEAD = 14         # 오늘부터 며칠 뒤까지 확인할지
WANT_WEEKDAYS = (2,)   # 알림 받을 상영 요일: 수=2

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
        data={"chat_id": CHAT_ID, "text": text[:4000]},
        timeout=20,
    )
    r.raise_for_status()


def fetch(ds, retries=3):
    data = {
        "masterType": "brch", "detailType": "area", "brchNo": BRCH_NO,
        "firstAt": "N", "brchNo1": BRCH_NO, "crtDe": ds, "playDe": ds,
    }
    last = None
    for _ in range(retries):
        try:
            r = requests.post(URL, data=data, headers=HEADERS, timeout=(12, 25))
            r.raise_for_status()
            j = r.json()
            return j.get("megaMap", {}).get("movieFormList", []) or []
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


def is_open(it):
    for f in ("bokdAbleAt", "bokdAbleYn", "bookAbleAt", "rsrvAbleAt"):
        if str(it.get(f, "")).upper() == "N":
            return False
    return True


try:
    with open(STATE_FILE, encoding="utf-8") as f:
        seen = set(json.load(f))
except Exception:
    seen = set()

today = now.date()
today_s = today.strftime("%Y%m%d")
all_seen = set(k for k in seen if k.split("|")[0] >= today_s)

LOOK = 21 if MANUAL else MAX_AHEAD
kw_by_date = {}
errors = 0
consec = 0
fetched_any = False

for i in range(LOOK + 1):
    ds = (today + datetime.timedelta(days=i)).strftime("%Y%m%d")
    try:
        items = fetch(ds)
        consec = 0
        fetched_any = True
    except Exception as e:
        errors += 1
        consec += 1
        print("조회 실패", ds, repr(e)[:100])
        if consec >= 3:
            break
        continue
    kws = [it for it in items if any(k in it.get("movieNm", "") for k in KEYWORDS)]
    if kws:
        kw_by_date[ds] = kws

if not fetched_any:
    print("조회 실패 (연결 안 됨)")
    sys.exit(1)

# 오늘부터 빈틈없이 이어진 날짜만 '예매 열림'으로 본다
run = []
started = False
for i in range(MAX_AHEAD + 1):
    ds = (today + datetime.timedelta(days=i)).strftime("%Y%m%d")
    if ds in kw_by_date:
        run.append(ds)
        started = True
    elif started or i > 1:
        break

if MANUAL:
    rows = []
    for ds in sorted(kw_by_date):
        its = kw_by_date[ds]
        ev = ",".join(sorted({str(x.get("eventProgrs")) for x in its}))
        ba = ",".join(sorted({str(x.get("bokdAbleAt")) for x in its}))
        rest = sum(int(x.get("restSeatCnt") or 0) for x in its)
        tot = sum(int(x.get("totSeatCnt") or 0) for x in its)
        mark = "열림" if ds in run else "제외"
        rows.append(f"{ds[4:6]}/{ds[6:]} {mark} {len(its)}건 ev={ev} ok={ba} 좌석{rest}/{tot}")
    last_run = run[-1] if run else "-"
    tg(
        f"✅ 메가박스 진단 실행\n열린 것으로 본 마지막 날짜: {last_run[4:6]}/{last_run[6:]}\n"
        f"조회 실패: {errors}건\n" + "\n".join(rows[:15])
    )
    print("진단 완료 (상태 저장 안 함)")
    sys.exit(0)

new_by_date = {}
for ds in run:
    d = datetime.datetime.strptime(ds, "%Y%m%d").date()
    if d.weekday() not in WANT_WEEKDAYS:
        continue
    for it in kw_by_date[ds]:
        if not is_open(it):
            continue
        st = str(it.get("playStartTime", ""))
        if ds == today_s and st and st < now.strftime("%H:%M"):
            continue
        key = f"{ds}|v6|{it.get('playSchdlNo') or (it.get('movieNm', '') + st)}"
        all_seen.add(key)
        if key not in seen:
            new_by_date.setdefault(ds, []).append(it)

first_run = "__seeded__" not in seen
all_seen.add("__seeded__")

if first_run:
    n = sum(len(v) for v in new_by_date.values())
    tg(f"🔧 메가박스 기준 저장 완료\n지금 열려 있는 수요일 회차 {n}개를 기억했어요.\n이제부터 새로 열리는 것만 알려드릴게요.")
else:
    for ds in sorted(new_by_date):
        d = datetime.datetime.strptime(ds, "%Y%m%d")
        lines = ["🎬 메가박스 금정AK플라자 예매 오픈!", f"{d.month}/{d.day}({WD[d.weekday()]})"]
        by_movie = {}
        for it in new_by_date[ds]:
            by_movie.setdefault(it.get("movieNm", ""), []).append(it)
        for name, its in by_movie.items():
            lines.append(f"\n[{name}]")
            for it in sorted(its, key=lambda x: str(x.get("playStartTime"))):
                st = str(it.get("playStartTime", ""))
                theab = it.get("theabEngNm") or it.get("theabExpoNm") or ""
                lines.append(f"- {st} {theab}".strip())
        lines.append("\n예매: https://www.megabox.co.kr/booking")
        tg("\n".join(lines))

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(sorted(all_seen), f, ensure_ascii=False, indent=0)
print("완료")
