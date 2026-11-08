import os, sys, json, time, datetime
import requests

KST = datetime.timezone(datetime.timedelta(hours=9))
now = datetime.datetime.now(KST)

BRCH_NO = os.environ["BRCH_NO"]
KEYWORDS = [k.strip() for k in os.environ.get("KEYWORDS", "치이카와").split(",") if k.strip()]
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "21"))
TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
MANUAL = os.environ.get("EVENT_NAME") == "workflow_dispatch"
AREA = os.environ.get("AREA_CD", "30")

WANT_WEEKDAYS = (2,)  # 알림 받을 상영 요일: 수=2

if not MANUAL and now.weekday() not in (2, 5):
    print("수/토가 아니라 종료")
    sys.exit(0)

STATE_FILE = "state.json"
BOOK_URL = "https://www.megabox.co.kr/on/oh/ohb/SimpleBooking/selectBokdList.do"
SCHED_URL = "https://www.megabox.co.kr/on/oh/ohc/Brch/schedulePage.do"
UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
H_JSON = {
    "User-Agent": UA,
    "Referer": "https://www.megabox.co.kr/booking",
    "X-Requested-With": "XMLHttpRequest",
    "Content-Type": "application/json; charset=UTF-8",
    "Accept": "application/json",
}
H_FORM_BOOK = dict(H_JSON, **{"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"})
H_FORM_SCHED = dict(
    H_FORM_BOOK, **{"Referer": f"https://www.megabox.co.kr/theater?brchNo={BRCH_NO}"}
)
WD = "월화수목금토일"


def tg(text):
    r = requests.post(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        data={"chat_id": CHAT_ID, "text": text[:4000]},
        timeout=20,
    )
    r.raise_for_status()


def post(url, retries=3, **kw):
    last = None
    for _ in range(retries):
        try:
            r = requests.post(url, timeout=(12, 25), **kw)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last = e
            time.sleep(3)
    raise last


def build(ds, v):
    p = {
        "arrMovieNo": "", "areaCd1": "", "areaCd2": "", "areaCd3": "",
        "brchAll": "", "brchNo1": BRCH_NO, "brchNo2": "", "brchNo3": "",
        "brchNoListCnt": 1, "brchSpcl": "",
        "movieNo1": "", "movieNo2": "", "movieNo3": "",
        "playDe": ds, "sellChnlCd": "",
        "spclbYn1": "", "spclbYn2": "", "spclbYn3": "",
        "theabKindCd1": "", "theabKindCd2": "", "theabKindCd3": "",
    }
    if v in (0, 3):
        p["areaCd1"] = AREA
    if v == 1:
        p["areaCd1"] = AREA
        p["spclbYn1"] = "N"
        p["theabKindCd1"] = "N"
    if v == 2:
        p["areaCd1"] = AREA
        p["brchAll"] = "N"
        p["brchSpcl"] = "N"
        p["spclbYn1"] = "N"
        p["theabKindCd1"] = "N"
    if v == 4:
        p["spclbYn1"] = "N"
        p["theabKindCd1"] = "N"
    return p


def fetch(ds, v, retries=3):
    p = build(ds, v)
    if v == 3:
        j = post(BOOK_URL, retries=retries, data=p, headers=H_FORM_BOOK)
    else:
        j = post(BOOK_URL, retries=retries, json=p, headers=H_JSON)
    if j.get("statCd") == -1:
        raise RuntimeError("statCd -1: " + str(j.get("msg")))
    lst = j.get("movieFormList") or j.get("megaMap", {}).get("movieFormList") or []
    return lst, j


def sched_items(ds):
    data = {
        "masterType": "brch", "detailType": "area", "brchNo": BRCH_NO,
        "firstAt": "N", "brchNo1": BRCH_NO, "crtDe": ds, "playDe": ds,
    }
    j = post(SCHED_URL, retries=2, data=data, headers=H_FORM_SCHED)
    return j.get("megaMap", {}).get("movieFormList", []) or []


def kw_items(items):
    return [it for it in items if any(k in it.get("movieNm", "") for k in KEYWORDS)]


def send_diagnostics(probe_lines):
    tg("❌ 예매 조회 방식을 못 찾았어요\n" + "\n".join(probe_lines))
    today = now.date()
    rows = []
    near = far = None
    for i in range(0, 16):
        d = today + datetime.timedelta(days=i)
        ds = d.strftime("%Y%m%d")
        try:
            its = kw_items(sched_items(ds))
        except Exception as e:
            rows.append(f"{ds[4:6]}/{ds[6:]}: 오류")
            continue
        first = min((str(x.get("playStartTime")) for x in its), default="-")
        rest = sum(int(x.get("restSeatCnt") or 0) for x in its)
        tot = sum(int(x.get("totSeatCnt") or 0) for x in its)
        rows.append(f"{ds[4:6]}/{ds[6:]}({WD[d.weekday()]}): {len(its)}건, 첫 {first}, 좌석 {rest}/{tot}")
        if its and i == 1:
            near = its[0]
        if its and i == 15:
            far = its[0]
    tg("📋 날짜별 치이카와(극장 페이지 데이터)\n" + "\n".join(rows))
    if near and far:
        diff = []
        for k in sorted(set(near) | set(far)):
            if near.get(k) != far.get(k):
                diff.append(f"{k}: {str(near.get(k))[:30]} | {str(far.get(k))[:30]}")
        tg("🔍 가까운 날짜 vs 먼 날짜 차이\n" + "\n".join(diff[:40]))
        tg("샘플(가까운 날짜):\n" + json.dumps(near, ensure_ascii=False))


try:
    with open(STATE_FILE, encoding="utf-8") as f:
        seen = set(json.load(f))
except Exception:
    seen = set()

today = now.date()
today_s = today.strftime("%Y%m%d")

good = None
probe_lines = []
for v in range(5):
    for off in (1, 0):
        ds = (today + datetime.timedelta(days=off)).strftime("%Y%m%d")
        try:
            lst, raw = fetch(ds, v, retries=2)
            if lst:
                good = v
                break
            probe_lines.append(f"v{v}/{ds[4:]}: 0건 키={','.join(list(raw.keys())[:6])}")
        except Exception as e:
            probe_lines.append(f"v{v}/{ds[4:]}: {repr(e)[:70]}")
    if good is not None:
        break

if good is None:
    print("\n".join(probe_lines))
    if MANUAL:
        send_diagnostics(probe_lines)
    sys.exit(1)

all_seen = set(k for k in seen if k.split("|")[0] >= today_s)
new_by_date = {}
match_dates = {}
sample = None
errors = 0

for i in range(DAYS_AHEAD + 1):
    d = today + datetime.timedelta(days=i)
    ds = d.strftime("%Y%m%d")
    try:
        items, _ = fetch(ds, good)
    except Exception as e:
        errors += 1
        print("조회 실패", ds, repr(e))
        continue
    for it in items:
        name = it.get("movieNm", "")
        if not any(k in name for k in KEYWORDS):
            continue
        if sample is None:
            sample = it
        match_dates[ds] = match_dates.get(ds, 0) + 1
        if d.weekday() not in WANT_WEEKDAYS:
            continue
        st = str(it.get("playStartTime", ""))
        if ds == today_s and st and st < now.strftime("%H:%M"):
            continue
        key = f"{ds}|v4|{it.get('playSchdlNo') or (name + st)}"
        all_seen.add(key)
        if key not in seen:
            new_by_date.setdefault(ds, []).append(it)

if errors == DAYS_AHEAD + 1:
    print("전부 조회 실패")
    sys.exit(1)

if MANUAL:
    dates_txt = ", ".join(f"{ds[4:6]}/{ds[6:]}" for ds in sorted(match_dates)) or "없음"
    tg(f"✅ 진단 실행 (조회 방식 v{good})\n치이카와 예매 열린 날짜: {dates_txt}\n조회 실패: {errors}건")
    if sample:
        tg("DEBUG 샘플:\n" + json.dumps(sample, ensure_ascii=False))
    print("진단 완료 (상태 저장 안 함)")
    sys.exit(0)

first_run = "__seeded__" not in seen
all_seen.add("__seeded__")

if first_run:
    n = sum(len(v) for v in new_by_date.values())
    tg(f"🔧 기준 저장 완료\n지금 열려 있는 수요일 회차 {n}개를 기억했어요.\n이제부터 새로 열리는 것만 알려드릴게요.")
else:
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
                theab = it.get("theabExpoNm") or it.get("theabEngNm") or it.get("theabNm") or ""
                rest, tot = it.get("restSeatCnt"), it.get("totSeatCnt")
                seats = f" ({rest}/{tot})" if rest is not None and tot is not None else ""
                lines.append(f"- {st} {theab}{seats}".strip())
        lines.append("\n예매: https://www.megabox.co.kr/booking")
        tg("\n".join(lines))

with open(STATE_FILE, "w", encoding="utf-8") as f:
    json.dump(sorted(all_seen), f, ensure_ascii=False, indent=0)
print("완료")
