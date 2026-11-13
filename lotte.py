import os, sys, json, time, datetime
import requests

KST = datetime.timezone(datetime.timedelta(hours=9))
now = datetime.datetime.now(KST)

CINEMA_ID = os.environ["LOTTE_CINEMA_ID"]
KEYWORDS = [k.strip() for k in os.environ.get("KEYWORDS", "치이카와").split(",") if k.strip()]
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MANUAL = os.environ.get("EVENT_NAME") == "workflow_dispatch"

MAX_AHEAD = 14         # 오늘부터 며칠 뒤까지 열린 것으로 볼지
WANT_WEEKDAYS = (2,)   # 알림 받을 상영 요일: 수=2

if not MANUAL and now.weekday() not in (2,):
    print("수요일이 아니라 종료")
    sys.exit(0)

STATE_FILE = "lotte_state.json"
URL = "https://www.lottecinema.co.kr/LCWS/Ticketing/TicketingData.aspx"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HEADERS = {
    "User-Agent": UA,
    "Referer": "https://www.lottecinema.co.kr/NLCMW/Ticketing",
    "Origin": "https://www.lottecinema.co.kr",
}
WD = "월화수목금토일"


def tg(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text[:4000]},
        timeout=20,
    )
    r.raise_for_status()


def extract(j):
    ps = j.get("PlaySeqs") if isinstance(j, dict) else None
    if isinstance(ps, dict):
        return ps.get("Items") or []
    return []


def fetch(ds, retries=3):
    param = {
        "MethodName": "GetPlaySequence", "channelType": "HO", "osType": "Chrome",
        "osVersion": UA, "playDate": ds, "cinemaID": CINEMA_ID,
        "representationMovieCode": "",
    }
    last = None
    for _ in range(retries):
        for key in ("paramList", "ParamList"):
            try:
                r = requests.post(URL, data={key: json.dumps(param)}, headers=HEADERS, timeout=(12, 25))
                r.raise_for_status()
                j = json.loads(r.text.lstrip("\ufeff"))
            except Exception as e:
                last = e
                continue
            if extract(j) or "ParamList" not in str(j.get("ResultMessage", "")):
                return extract(j), j
        time.sleep(3)
    raise last or RuntimeError("조회 실패")


def hm(s):
    digits = "".join(c for c in str(s) if c.isdigit())
    return digits.zfill(4)


def fmt(s):
    h = hm(s)
    return f"{h[:2]}:{h[2:]}"


def is_open(it):
    return str(it.get("IsBookingYN", "Y")).upper() != "N"


def sig(items):
    return tuple(sorted((str(x.get("MovieNameKR", "")), hm(x.get("StartTime")), str(x.get("ScreenNameKR", ""))) for x in items))


try:
    with open(STATE_FILE, encoding="utf-8") as f:
        seen = set(json.load(f))
except Exception:
    seen = set()

today = now.date()
today_s = today.strftime("%Y-%m-%d")
all_seen = set(k for k in seen if k.split("|")[0] >= today_s or k.startswith("__"))

LOOK = 21 if MANUAL else MAX_AHEAD
by_date = {}
errors = 0
consec = 0
first_raw = None
last_err = None

for i in range(LOOK + 1):
    ds = (today + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
    try:
        items, raw = fetch(ds)
        consec = 0
    except Exception as e:
        errors += 1
        consec += 1
        last_err = repr(e)[:150]
        print("조회 실패", ds, last_err)
        if consec >= 3:
            break
        continue
    if first_raw is None:
        first_raw = raw
    by_date[ds] = items

if not by_date:
    msg = f"❌ 롯데시네마 조회 실패\n{last_err}"
    print(msg)
    if MANUAL:
        tg(msg)
    sys.exit(1)

if not any(by_date.values()):
    msg = "❌ 시간표가 비어 있어요\n" + json.dumps(first_raw, ensure_ascii=False)[:800]
    print(msg)
    if MANUAL:
        tg(msg)
    sys.exit(1)

# 오늘부터 이어진 날짜만 '예매 열림'으로 본다 (같은 시간표가 반복되면 가짜로 보고 중단)
run = []
sigs = set()
for i in range(MAX_AHEAD + 1):
    ds = (today + datetime.timedelta(days=i)).strftime("%Y-%m-%d")
    items = by_date.get(ds)
    if not items:
        if i == 0:
            continue
        break
    s = sig(items)
    if s in sigs:
        break
    sigs.add(s)
    run.append(ds)


def kw(items):
    return [it for it in items if any(k in str(it.get("MovieNameKR", "")) for k in KEYWORDS)]


if MANUAL:
    rows = []
    for ds in sorted(by_date):
        its = by_date[ds]
        k = kw(its)
        mark = "열림" if ds in run else "제외"
        first = min((hm(x.get("StartTime")) for x in k), default="-")
        rest = sum(int(x.get("BookingSeatCount") or 0) for x in k)
        tot = sum(int(x.get("TotalSeatCount") or 0) for x in k)
        ok = ",".join(sorted({str(x.get("IsBookingYN")) for x in k})) or "-"
        rows.append(f"{ds[5:]} {mark} 전체{len(its)} 치이카와{len(k)} 첫{first} ok={ok} 좌석{rest}/{tot}")
    last_run = run[-1] if run else "-"
    tg(f"✅ 롯데 진단 실행\n열린 것으로 본 마지막 날짜: {last_run[5:]}\n조회 실패: {errors}건\n" + "\n".join(rows))
    first_ds = run[0] if run else sorted(by_date)[0]
    titles = sorted({str(x.get("MovieNameKR", "")) for x in by_date.get(first_ds, [])})
    tg(f"🎞 {first_ds[5:]} 상영작 목록\n" + "\n".join(titles[:25]))
    sample = None
    for ds in sorted(by_date):
        if kw(by_date[ds]):
            sample = kw(by_date[ds])[0]
            break
    if sample:
        tg("DEBUG 샘플:\n" + json.dumps(sample, ensure_ascii=False))
    print("진단 완료 (상태 저장 안 함)")
    sys.exit(0)

new_by_date = {}
for ds in run:
    d = datetime.datetime.strptime(ds, "%Y-%m-%d").date()
    if d.weekday() not in WANT_WEEKDAYS:
        continue
    for it in kw(by_date[ds]):
        if not is_open(it):
            continue
        st = hm(it.get("StartTime"))
        if ds == today_s and st < now.strftime("%H%M"):
            continue
        key = f"{ds}|l1|{it.get('ScreenID', '')}|{it.get('PlaySequence', '')}|{st}"
        all_seen.add(key)
        if key not in seen:
            new_by_date.setdefault(ds, []).append(it)

first_run = "__seeded__" not in seen
all_seen.add("__seeded__")

if first_run:
    n = sum(len(v) for v in new_by_date.values())
    tg(f"🔧 롯데 기준 저장 완료\n지금 열려 있는 수요일 회차 {n}개를 기억했어요.\n이제부터 새로 열리는 것만 알려드릴게요.")
else:
    for ds in sorted(new_by_date):
        d = datetime.datetime.strptime(ds, "%Y-%m-%d")
        lines = ["🎬 롯데시네마 평촌(범계) 예매 오픈!", f"{d.month}/{d.day}({WD[d.weekday()]})"]
        by_movie = {}
        for it in new_by_date[ds]:
            by_movie.setdefault(str(it.get("MovieNameKR", "")), []).append(it)
        for name, its in by_movie.items():
            lines.append(f"\n[{name}]")
            for it in sorted(its, key=lambda x: hm(x.get("StartTime"))):
                scr = it.get("ScreenNameKR") or ""
                rest, tot = it.get("BookingSeatCount"), it.get("TotalSeatCount")
                seats = f" ({rest}/{tot})" if rest is not None and tot is not None else ""
                lines.append(f"- {fmt(it.get('StartTime'))} {scr}{seats}".strip())
        lines.append("\n예매: https://www.lottecinema.co.kr")
        tg("\n".join(lines))

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(sorted(all_seen), f, ensure_ascii=False, indent=0)
print("완료")
