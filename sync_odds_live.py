import os
import re
import time
import requests
import numpy as np
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from supabase import create_client

# ==============================================================================
# ⚡ 香港賽馬 AI：15秒極速「臨場排位變動 + 即時賠率 + 聰明錢落飛 + AI 同日動態賽道偏差」同步引擎
# 專為賽前一日開盤後及賽日臨場設計：
# 1. 開盤守門員：賽前一日正午 12:00 前未開盤時，自動攔截舊賠率並清除誤報聰明錢標籤。
# 2. 排位動態同步：自動檢測當日退出馬 (Scratchings)、騎師臨時易配及最新排位體重。
# 3. 嚴格日期核對：東網鏡像強制核對賽日日期，100% 杜絕跨賽日舊賠率張冠李戴。
# 4. AI 貝葉斯同日賽道偏差：每場跑完後自動根據草地/泥地、彎道/直路、場次信度與時間衰減，動態加權後續場次！
# 5. Harville 位置理論概率 + 對數線性概率池化 (Log-Linear Pooling)：與 predict_upcoming.py 100% 數學對齊。
# ==============================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rxmkohhgznfcnhdqegwq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.hkjc.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

_SHARED_DRIVER = None

def is_odds_pool_open(target_date_str):
    """馬會固定於賽事前一日中午 12:00 (HKT) 開盤，11:30 AM 前不抓賠率、不開 Chrome"""
    try:
        hkt = timezone(timedelta(hours=8))
        now_hkt = datetime.now(hkt)
        race_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        open_dt = datetime(race_dt.year, race_dt.month, race_dt.day, 11, 30, tzinfo=hkt) - timedelta(days=1)
        close_dt = datetime(race_dt.year, race_dt.month, race_dt.day, 23, 55, tzinfo=hkt)
        return open_dt <= now_hkt <= close_dt
    except Exception:
        return True

def compute_harville_place_probs(win_probs_pct, num_places=3):
    """學術級 Harville (1973) 理論位置概率推算器"""
    p = np.clip(np.array(win_probs_pct, dtype=float) / 100.0, 1e-5, 0.999)
    p = p / p.sum()
    n = len(p)
    place_probs = np.zeros(n, dtype=float)
    for i in range(n):
        p1 = p[i]
        p2 = 0.0
        for j in range(n):
            if j == i:
                continue
            denom1 = max(1e-6, 1.0 - p[j])
            p2 += p[j] * (p[i] / denom1)
        p3 = 0.0
        if num_places >= 3 and n >= 3:
            for j in range(n):
                if j == i:
                    continue
                denom1 = max(1e-6, 1.0 - p[j])
                for k in range(n):
                    if k == i or k == j:
                        continue
                    denom2 = max(1e-6, 1.0 - p[j] - p[k])
                    p3 += p[j] * (p[k] / denom1) * (p[i] / denom2)
        place_probs[i] = (p1 + p2 + (p3 if num_places >= 3 else 0.0)) * 100.0
    return place_probs

def detect_intraday_track_bias(date_hkjc, venue):
    """
    📡 AI 貝葉斯自適應賽道偏差引擎 (與 predict_upcoming.py 同步：分草泥、分彎直、場次信度與時間衰減)
    """
    completed_list = []

    for r_no in range(1, 12):
        url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_hkjc}&Racecourse={venue}&RaceNo={r_no}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=5)
            if resp.status_code != 200 or "沒有相關資料" in resp.text:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            race_txt = soup.get_text()

            perf_tb = None
            for tb in soup.find_all("table"):
                txt_h = tb.get_text()
                if "名次" in txt_h and "馬名" in txt_h and "檔位" in txt_h:
                    perf_tb = tb
                    break
            if not perf_tb:
                break

            dist_m = re.search(r"(\d{3,4})\s*米", race_txt)
            r_dist = int(dist_m.group(1)) if dist_m else 1200
            is_awt = ("全天候" in race_txt or "泥地" in race_txt)
            is_st_str = (not is_awt and venue == "ST" and r_dist == 1000)
            cat = "AWT" if is_awt else ("TURF_STRAIGHT" if is_st_str else "TURF_TURN")

            headers = [th.get_text(strip=True) for th in perf_tb.find("tr").find_all(["td", "th"])]
            draw_idx = next((i for i, h in enumerate(headers) if "檔位" in h), 7)
            pos_idx = next((i for i, h in enumerate(headers) if "沿途走位" in h), 9)

            top_runners = []
            for tr in perf_tb.find_all("tr")[1:]:
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(tds) <= max(draw_idx, 2):
                    continue
                pl_str = tds[0].strip()
                if pl_str in ("1", "2", "3", "4", "01", "02", "03", "04"):
                    pl_num = int(pl_str)
                    pl_wt = 1.5 if pl_num == 1 else (1.2 if pl_num == 2 else (1.0 if pl_num == 3 else 0.8))
                    d_str = tds[draw_idx].strip()
                    d_val = int(d_str) if d_str.isdigit() else 7
                    start_pos = None
                    if len(tds) > pos_idx:
                        pos_parts = [int(x) for x in tds[pos_idx].split() if x.isdigit()]
                        if pos_parts:
                            start_pos = pos_parts[0]
                    top_runners.append({
                        "place": pl_num,
                        "weight": pl_wt,
                        "draw": d_val,
                        "start_pos": start_pos
                    })
            if len(top_runners) >= 3:
                completed_list.append({
                    "race_no": r_no,
                    "category": cat,
                    "distance": r_dist,
                    "runners": top_runners
                })
            else:
                break
        except Exception:
            break

    def _compute_cat_bias(cat_races):
        n_races = len(cat_races)
        if n_races == 0:
            return {
                "races": 0, "confidence": 0.0,
                "inside_boost": 0.0, "wide_boost": 0.0,
                "front_boost": 0.0, "closer_boost": 0.0,
                "summary": "待開跑"
            }
        conf = 0.38 if n_races == 1 else (0.72 if n_races == 2 else 1.00)
        inside_w, wide_w, front_w, closer_w, total_w = 0.0, 0.0, 0.0, 0.0, 0.0

        for idx, rc in enumerate(cat_races):
            recency_wt = 0.82 ** (n_races - 1 - idx)
            for rn in rc["runners"]:
                w = rn["weight"] * recency_wt
                total_w += w
                if rn["draw"] <= 6:
                    inside_w += w
                elif rn["draw"] >= 8:
                    wide_w += w
                if rn["start_pos"] is not None:
                    if rn["start_pos"] <= 4:
                        front_w += w
                    elif rn["start_pos"] >= 7:
                        closer_w += w

        in_ratio = inside_w / max(total_w, 1e-5)
        wi_ratio = wide_w / max(total_w, 1e-5)
        fr_ratio = front_w / max(total_w, 1e-5)
        cl_ratio = closer_w / max(total_w, 1e-5)

        in_boost, wi_boost, fr_boost, cl_boost = 0.0, 0.0, 0.0, 0.0
        notes = []

        if in_ratio >= 0.56:
            raw_b = min(0.12, 0.05 + (in_ratio - 0.56) * 0.25)
            in_boost = round(raw_b * conf, 3)
            notes.append(f"內中檔(1-6檔)佔比{int(in_ratio*100)}%(+{in_boost:.2f})")
        elif wi_ratio >= 0.50:
            raw_b = min(0.11, 0.05 + (wi_ratio - 0.50) * 0.22)
            wi_boost = round(raw_b * conf, 3)
            notes.append(f"中外疊(8檔+)佔比{int(wi_ratio*100)}%(+{wi_boost:.2f})")

        if fr_ratio >= 0.52:
            raw_b = min(0.12, 0.05 + (fr_ratio - 0.52) * 0.25)
            fr_boost = round(raw_b * conf, 3)
            notes.append(f"前置放頭(頭段前4)佔比{int(fr_ratio*100)}%(+{fr_boost:.2f})")
        elif cl_ratio >= 0.45:
            raw_b = min(0.12, 0.05 + (cl_ratio - 0.45) * 0.25)
            cl_boost = round(raw_b * conf, 3)
            notes.append(f"後追衝刺(頭段7位後)佔比{int(cl_ratio*100)}%(+{cl_boost:.2f})")

        return {
            "races": n_races,
            "confidence": conf,
            "inside_boost": in_boost,
            "wide_boost": wi_boost,
            "front_boost": fr_boost,
            "closer_boost": cl_boost,
            "summary": f"已跑{n_races}場(信度{int(conf*100)}%): " + (" | ".join(notes) if notes else "偏差均衡")
        }

    turf_turn_bias = _compute_cat_bias([x for x in completed_list if x["category"] == "TURF_TURN"])
    awt_bias = _compute_cat_bias([x for x in completed_list if x["category"] == "AWT"])
    straight_bias = _compute_cat_bias([x for x in completed_list if x["category"] == "TURF_STRAIGHT"])

    summary_parts = []
    if turf_turn_bias["races"] > 0:
        summary_parts.append(f"[草地彎道] {turf_turn_bias['summary']}")
    if awt_bias["races"] > 0:
        summary_parts.append(f"[泥地] {awt_bias['summary']}")
    if straight_bias["races"] > 0:
        summary_parts.append(f"[直路] {straight_bias['summary']}")

    return {
        "completed_races": len(completed_list),
        "TURF_TURN": turf_turn_bias,
        "AWT": awt_bias,
        "TURF_STRAIGHT": straight_bias,
        "summary": " ； ".join(summary_parts) if summary_parts else "尚未有已完成賽事"
    }

def get_shared_driver():
    global _SHARED_DRIVER
    if _SHARED_DRIVER is not None:
        return _SHARED_DRIVER
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        try:
            _SHARED_DRIVER = webdriver.Chrome(options=chrome_options)
        except Exception:
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            _SHARED_DRIVER = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        return _SHARED_DRIVER
    except Exception:
        return None

def fetch_racecard_updates(target_date, venue, race_no):
    """快速檢查排位表最新變動：退出馬、臨時易配騎師、最新負磅"""
    date_slash = target_date.replace("-", "/")
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={date_slash}&Racecourse={venue}&RaceNo={race_no}"
    active_horses = {}
    scratched_nos = set()
    try:
        r = requests.get(url, headers=HEADERS, timeout=6)
        if r.status_code != 200:
            return active_horses, scratched_nos
        soup = BeautifulSoup(r.text, "html.parser")
        for tb in soup.find_all("table"):
            header_tr = tb.find("tr")
            if not header_tr:
                continue
            headers = [th.text.strip() for th in header_tr.find_all(["th", "td"])]
            h_idx = {}
            for i, h in enumerate(headers):
                if ("馬匹編號" in h or "馬號" in h) and "no" not in h_idx: h_idx["no"] = i
                elif "馬名" in h and "綵衣" not in h and "name" not in h_idx: h_idx["name"] = i
                elif "負磅" in h and "+/-" not in h and "wt" not in h_idx: h_idx["wt"] = i
                elif "騎師" in h and "jk" not in h_idx: h_idx["jk"] = i
                elif "練馬師" in h and "tr" not in h_idx: h_idx["tr"] = i
                elif "檔位" in h and "dr" not in h_idx: h_idx["dr"] = i
                elif "評分" in h and "rt" not in h_idx: h_idx["rt"] = i
            if "no" not in h_idx or "name" not in h_idx:
                continue
            for tr in tb.find_all("tr")[1:]:
                tds = [td.text.strip() for td in tr.find_all(["td", "th"])]
                if len(tds) <= h_idx["no"] or not tds[h_idx["no"]].isdigit():
                    continue
                h_no = int(tds[h_idx["no"]])
                row_txt = " ".join(tds)
                if "退出" in row_txt or "Withdrawn" in row_txt or "SCR" in row_txt:
                    scratched_nos.add(h_no)
                    continue
                jk = tds[h_idx["jk"]] if "jk" in h_idx and len(tds) > h_idx["jk"] else ""
                tr_name = tds[h_idx["tr"]] if "tr" in h_idx and len(tds) > h_idx["tr"] else ""
                wt_val = None
                if "wt" in h_idx and len(tds) > h_idx["wt"]:
                    m_w = re.search(r"\d+", tds[h_idx["wt"]])
                    if m_w: wt_val = int(m_w.group(0))
                rt_val = None
                if "rt" in h_idx and len(tds) > h_idx["rt"]:
                    m_r = re.search(r"\d+", tds[h_idx["rt"]])
                    if m_r: rt_val = int(m_r.group(0))
                active_horses[h_no] = {
                    "jockey": f"{jk} / {tr_name}" if tr_name else jk,
                    "weight": wt_val,
                    "rating": rt_val
                }
    except Exception:
        pass
    return active_horses, scratched_nos

def sanitize_wp_odds(win_val, pla_val):
    """
    嚴格驗證獨贏與位置賠率合理性：
    1. 獨贏必須在 1.01 ~ 999.0 之間
    2. 位置必須在 1.01 ~ 99.0 之間，且位置賠率不可大於獨贏賠率 (防止 128磅 負磅混入位置賠率)
    """
    try:
        w = float(win_val) if (win_val is not None and 1.01 <= float(win_val) <= 999.0) else None
    except Exception:
        w = None
    try:
        p = float(pla_val) if (pla_val is not None and 1.01 <= float(pla_val) <= 99.0) else None
    except Exception:
        p = None
    if w and p and p > w and w >= 1.5:
        p = None
    if w is None and p is not None and p >= 35.0:
        p = None
    return w, p

def fetch_live_odds_fast(target_date, venue, race_no):
    """三層極速賠率通道 (具備嚴格賽日核對與防負磅/減磅污染濾網)"""
    odds_map = {}
    if not is_odds_pool_open(target_date):
        return odds_map

    # 1. 馬會官方 getJSON.aspx
    urls = [
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=winplaodds&date={target_date}&venue={venue}&raceno={race_no}",
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=win&date={target_date}&venue={venue}&raceno={race_no}"
    ]
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=3.5)
            if r.status_code == 200 and r.text and "=" in r.text:
                tokens = r.text.replace("&", ";").split(";")
                for tok in tokens:
                    if "=" in tok:
                        parts = tok.split("=")
                        if len(parts) == 2 and parts[0].strip().isdigit():
                            h_no = int(parts[0].strip())
                            vals = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", parts[1])]
                            if vals:
                                win_val = vals[0]
                                pla_val = vals[1] if len(vals) >= 2 else None
                                w_clean, p_clean = sanitize_wp_odds(win_val, pla_val)
                                if w_clean:
                                    odds_map[h_no] = {"win": w_clean, "pla": p_clean}
                if odds_map:
                    return odds_map
        except Exception:
            pass

    # 2. 東網即時盤鏡像 (必須核對頁面日期與 target_date 一致，且只讀最尾兩欄純數字)
    url_oncc = f"https://racing.on.cc/racing/rat/current/rjratb{race_no:04d}x0.html"
    try:
        r = requests.get(url_oncc, headers=HEADERS, timeout=3.5)
        if r.status_code == 200 and r.text:
            r.encoding = "big5"
            soup = BeautifulSoup(r.text, "html.parser")
            page_txt = soup.get_text()
            parts_d = target_date.split("-")
            valid_date = True
            if len(parts_d) == 3:
                y_s, m_s, d_s = parts_d[0], str(int(parts_d[1])), str(int(parts_d[2]))
                d_pad, m_pad = parts_d[2], parts_d[1]
                valid_date = any(pat in page_txt for pat in [
                    f"{d_pad}/{m_pad}/{y_s}", f"{d_s}/{m_s}/{y_s}",
                    f"{y_s}年{m_s}月{d_s}日", f"{y_s}-{m_pad}-{d_pad}"
                ])
            if valid_date:
                for tr in soup.find_all("tr"):
                    tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    if len(tds) >= 4 and tds[0].isdigit():
                        h_no = int(tds[0])
                        last_two = tds[-2:]
                        m1 = re.match(r"^\d+(\.\d+)?$", last_two[0].strip())
                        m2 = re.match(r"^\d+(\.\d+)?$", last_two[1].strip())
                        if m1 and m2:
                            w_clean, p_clean = sanitize_wp_odds(float(m1.group(0)), float(m2.group(0)))
                            if w_clean and p_clean:
                                odds_map[h_no] = {"win": w_clean, "pla": p_clean}
                if odds_map:
                    return odds_map
    except Exception:
        pass

    # 3. Selenium 單例共用視窗 (精準定位獨贏與位置欄，絕不把騎師 -2/-7 減磅或 128磅 當成賠率)
    driver = get_shared_driver()
    if driver:
        try:
            url = f"https://bet.hkjc.com/ch/racing/wp/{target_date}/{venue}/{race_no}"
            driver.set_page_load_timeout(10)
            driver.get(url)
            time.sleep(1.8)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            for tb in soup.find_all("table"):
                header_txt = tb.get_text()
                if "獨贏" not in header_txt and "位置" not in header_txt:
                    continue
                first_tr = tb.find("tr")
                headers = [th.get_text(strip=True) for th in first_tr.find_all(["th", "td"])] if first_tr else []
                win_idx = next((i for i, h in enumerate(headers) if "獨贏" in h or h.upper() == "WIN"), None)
                pla_idx = next((i for i, h in enumerate(headers) if "位置" in h or h.upper() in ("PLA", "PLACE")), None)

                for tr in tb.find_all("tr")[1:]:
                    tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                    if len(tds) < 3 or not tds[0].isdigit():
                        continue
                    h_no = int(tds[0])
                    if not (1 <= h_no <= 14):
                        continue
                    win_val, pla_val = None, None
                    if win_idx is not None and len(tds) > win_idx:
                        m_w = re.match(r'^(\d+(?:\.\d+)?)$', tds[win_idx].strip())
                        if m_w: win_val = float(m_w.group(1))
                    if pla_idx is not None and len(tds) > pla_idx:
                        m_p = re.match(r'^(\d+(?:\.\d+)?)$', tds[pla_idx].strip())
                        if m_p: pla_val = float(m_p.group(1))
                    if win_val is None and pla_val is None and len(tds) >= 2:
                        last_two = tds[-2:]
                        m1 = re.match(r'^(\d+(?:\.\d+)?)$', last_two[0].strip())
                        m2 = re.match(r'^(\d+(?:\.\d+)?)$', last_two[1].strip())
                        if m1 and m2:
                            win_val, pla_val = float(m1.group(1)), float(m2.group(1))
                    w_clean, p_clean = sanitize_wp_odds(win_val, pla_val)
                    if w_clean and p_clean:
                        odds_map[h_no] = {"win": w_clean, "pla": p_clean}
        except Exception:
            pass

    return odds_map

def sync_live_odds_and_ev():
    print("=" * 72)
    print("⚡ 香港賽馬 AI：臨場排位變動、即時賠率、聰明錢與 AI 同日賽道偏差秒刷引擎")
    print("=" * 72)

    r_res = sb.table("races").select("*").order("race_date", desc=True).limit(15).execute()
    if not r_res.data:
        print("❌ 未在資料庫中找到賽事資料，請先執行一次排位預測。")
        return
    
    target_date = r_res.data[0]["race_date"]
    venue = r_res.data[0]["venue"]
    date_clean = target_date.replace("-", "")
    date_hkjc = target_date.replace("-", "/")
    pool_open = is_odds_pool_open(target_date)

    race_meta_map = {row["race_id"]: row for row in r_res.data if row.get("race_date") == target_date}
    intraday_bias = detect_intraday_track_bias(date_hkjc, venue)
    print(f"🎯 目標賽日: {target_date} ({venue}) | 彩池狀態: {'🟢 已開盤' if pool_open else '⏸️ 未開盤'} | 賽道偏差: {intraday_bias['summary']}")

    preds_res = sb.table("race_predictions").select("*").ilike("race_id", f"{date_clean}_{venue}%").execute()
    if not preds_res.data:
        print("❌ 尚未生成 AI 預測數據，請先運行完整排位預測。")
        return

    races_dict = {}
    for row in preds_res.data:
        races_dict.setdefault(row["race_id"], []).append(row)

    total_updated_horses = 0

    for race_id in sorted(races_dict.keys()):
        race_no = int(race_id.split("_")[-1])
        horses = races_dict[race_id]
        if not horses:
            continue

        rm = race_meta_map.get(race_id, {})
        dist = int(rm.get("distance") or 1200)
        track_type = str(rm.get("track_type") or "草地")
        is_awt = ("全天候" in track_type or "泥" in track_type)
        is_st_straight = (not is_awt and venue == "ST" and dist == 1000)
        cat_key = "AWT" if is_awt else ("TURF_STRAIGHT" if is_st_straight else "TURF_TURN")
        active_bias = intraday_bias.get(cat_key) or {}

        # 1. 檢查排位表最新變動 (剔除退出馬、更新易配騎師)
        active_rc, scratched_nos = fetch_racecard_updates(target_date, venue, race_no)
        if scratched_nos:
            print(f"  [🚨 退出馬剔除] 第 {race_no} 場偵測到退出馬號: {sorted(list(scratched_nos))}")
            horses = [h for h in horses if h["horse_no"] not in scratched_nos]

        for h in horses:
            if h["horse_no"] in active_rc:
                rc_info = active_rc[h["horse_no"]]
                if rc_info.get("jockey"):
                    h["jockey"] = rc_info["jockey"]
                if rc_info.get("weight"):
                    h["weight"] = rc_info["weight"]
                if rc_info.get("rating"):
                    h["rating"] = rc_info["rating"]

        # 2. 若尚未開盤：清除誤存的舊賠率與假聰明錢標籤，重新歸一化勝率後入庫
        if not pool_open:
            raw_probs = np.array([float(h.get("win_probability") or 10.0) for h in horses])
            if raw_probs.sum() > 0:
                raw_probs = (raw_probs / raw_probs.sum()) * 100.0
            clean_unopened = []
            for i, h in enumerate(horses):
                tags = list(h.get("smart_tags") or [])
                tags = [
                    t for t in tags
                    if not any(kw in str(t) for kw in ["位置賠率", "正期望值", "建議注碼", "聰明錢", "大戶位置", "終極黃金狙擊", "回飛", "實時偏差"])
                ]
                w_p_val = round(float(raw_probs[i]), 2)
                fair_odd = round(min(99.0, max(1.5, 82.5 / max(w_p_val, 0.85))), 1)
                clean_unopened.append({
                    "race_id": race_id,
                    "horse_no": h["horse_no"],
                    "horse_code": h["horse_code"],
                    "horse_name": h["horse_name"],
                    "draw": h["draw"],
                    "jockey": h["jockey"],
                    "weight": h.get("weight", 125),
                    "win_probability": w_p_val,
                    "predicted_rank": i + 1,
                    "is_value_bet": (i < 3),
                    "gear": h["gear"],
                    "rating": h["rating"],
                    "smart_tags": list(set(tags)),
                    "combo_synergy": 0.0,
                    "bet_strategy": "🎯 獨贏首選 / 實力馬膽" if i == 0 else ("⚡ 次選主力 / 黃金走位" if i == 1 else ("🛡️ 連贏配腳 / 高爆發冷門" if i < 4 else "")),
                    "market_odds": fair_odd,
                    "rest_days": h.get("rest_days") if h.get("rest_days") is not None else 21,
                    "fast_gallop_count": h.get("fast_gallop_count") if h.get("fast_gallop_count") else 3
                })
            clean_unopened.sort(key=lambda x: x["win_probability"], reverse=True)
            for idx_r, item in enumerate(clean_unopened, 1):
                item["predicted_rank"] = idx_r
                item["bet_strategy"] = "🎯 獨贏首選 / 實力馬膽" if idx_r == 1 else ("⚡ 次選主力 / 黃金走位" if idx_r == 2 else ("🛡️ 連贏配腳 / 高爆發冷門" if idx_r <= 4 else ""))
            sb.table("race_predictions").delete().eq("race_id", race_id).execute()
            sb.table("race_predictions").insert(clean_unopened).execute()
            total_updated_horses += len(clean_unopened)
            print(f"  ✓ 第 {race_no} 場 [未開盤淨化完成]: 已清除舊盤污染並同步最新排位 ({len(clean_unopened)} 匹馬)")
            continue

        # 3. 已開盤：抓取真實即時賠率 + Harville 位置概率 + 貝葉斯同日賽道偏差動態注入
        odds_map = fetch_live_odds_fast(target_date, venue, race_no)
        if not odds_map:
            print(f"  • 第 {race_no} 場: 暫未抓到即時賠率")
            continue

        has_win = any(h["horse_no"] in odds_map and (odds_map[h["horse_no"]].get("win") or 0) > 1.0 for h in horses)
        if not has_win:
            continue

        num_places = 3 if len(horses) >= 7 else 2
        win_implied = np.array([1.0 / max(float(odds_map.get(h["horse_no"], {}).get("win", 20.0) or 20.0), 1.01) for h in horses])
        win_mkt_probs = (win_implied / win_implied.sum()) * 100.0
        harville_pla_probs = compute_harville_place_probs(win_mkt_probs, num_places=num_places)

        has_pla = any(h["horse_no"] in odds_map and (odds_map[h["horse_no"]].get("pla") or 0) > 1.0 for h in horses)
        if has_pla:
            pla_implied = np.array([1.0 / max(float(odds_map.get(h["horse_no"], {}).get("pla", 5.0) or 5.0), 1.01) for h in horses])
            pla_mkt_probs_300 = (pla_implied / pla_implied.sum()) * (num_places * 100.0)
            pla_mkt_probs_100 = pla_mkt_probs_300 / float(num_places)
            mkt_composite = 0.75 * win_mkt_probs + 0.25 * pla_mkt_probs_100
        else:
            pla_mkt_probs_300 = harville_pla_probs
            mkt_composite = win_mkt_probs

        # 讀取基礎勝率並注入最新同日實時賽道偏差 (若該場尚未跑完)
        base_probs = np.array([float(h.get("win_probability") or 10.0) for h in horses])
        bias_mults = np.ones(len(horses), dtype=float)
        new_bias_tags_map = {h["horse_no"]: [] for h in horses}

        if active_bias.get("races", 0) >= 1 and race_no > active_bias.get("races", 0):
            conf_pct = int(active_bias.get("confidence", 0.38) * 100)
            dist_front_mult = 1.15 if dist <= 1200 else 1.0
            dist_closer_mult = 1.15 if dist >= 1400 else 1.0

            for idx_h, h in enumerate(horses):
                h_no = h["horse_no"]
                dr = int(h.get("draw") or 7)
                tags_s = " ".join(str(t) for t in (h.get("smart_tags") or []))
                is_front = any(k in tags_s for k in ["單騎領放", "前領放頭", "跟前", "先手", "互搶力竭"])
                is_closer = any(k in tags_s for k in ["後勁", "末段狂追", "慢步速戰略受制", "末段極速"])
                b_bonus = 0.0

                if not is_st_straight:
                    if active_bias.get("inside_boost", 0) > 0 and dr <= 6:
                        b_bonus += active_bias["inside_boost"]
                        new_bias_tags_map[h_no].append(f"📡 AI實時偏差({conf_pct}%信度): 1-6內中檔利位")
                    elif active_bias.get("wide_boost", 0) > 0 and dr >= 8:
                        b_bonus += active_bias["wide_boost"]
                        new_bias_tags_map[h_no].append(f"📡 AI實時偏差({conf_pct}%信度): 中外疊跑線有利")

                if active_bias.get("front_boost", 0) > 0 and is_front:
                    b_bonus += round(active_bias["front_boost"] * dist_front_mult, 3)
                    new_bias_tags_map[h_no].append(f"📡 AI實時偏差({conf_pct}%信度): 場地利前置放頭")
                elif active_bias.get("closer_boost", 0) > 0 and is_closer:
                    b_bonus += round(active_bias["closer_boost"] * dist_closer_mult, 3)
                    new_bias_tags_map[h_no].append(f"📡 AI實時偏差({conf_pct}%信度): 場地利後追衝刺")

                bias_mults[idx_h] = float(np.exp(b_bonus * 1.5))

        adj_raw_probs = base_probs * bias_mults
        if adj_raw_probs.sum() > 0:
            adj_raw_probs = (adj_raw_probs / adj_raw_probs.sum()) * 100.0

        # 對數線性概率池化校準 (Log-Linear Opinion Pooling)
        safe_raw = np.clip(adj_raw_probs, 1e-3, 100.0)
        safe_mkt = np.clip(mkt_composite, 1e-3, 100.0)
        log_pooled = (safe_raw ** 0.80) * (safe_mkt ** 0.20)
        final_probs = (log_pooled / log_pooled.sum()) * 100.0

        updated_payload = []
        for i, h in enumerate(horses):
            h_no = h["horse_no"]
            odds_info = odds_map.get(h_no, {})
            win_odd = odds_info.get("win")
            pla_odd = odds_info.get("pla")

            tags = list(h.get("smart_tags") or [])
            tags = [
                t for t in tags
                if not str(t).startswith("位置賠率:")
                and not str(t).startswith("💎 正期望值")
                and not str(t).startswith("📊 建議注碼")
                and "實時偏差" not in str(t)
            ]
            tags.extend(new_bias_tags_map.get(h_no, []))

            # 檢查舊標籤中是否曾有真實位置賠率 (確保不會拿未開盤的 AI 公允賠率或污染數字來計落飛)
            old_raw_tags = list(h.get("smart_tags") or [])
            old_pla = None
            for ot in old_raw_tags:
                if "位置賠率:" in str(ot):
                    m_op = re.search(r"(\d+(?:\.\d+)?)", str(ot))
                    if m_op: old_pla = float(m_op.group(1))
            prev_win_clean, prev_pla_clean = sanitize_wp_odds(h.get("market_odds"), old_pla)

            if win_odd and win_odd > 1.0 and prev_win_clean and prev_pla_clean:
                drop_pct = ((prev_win_clean - win_odd) / prev_win_clean) * 100.0
                if drop_pct >= 20.0:
                    # 落飛時同時清除舊的落飛與回飛標籤，杜絕同時出現「落飛」與「回飛」矛盾標籤
                    tags = [t for t in tags if "聰明錢落飛" not in str(t) and "資金冷卻回飛" not in str(t)]
                    tags.append(f"🔥 聰明錢落飛 (-{int(drop_pct)}%)")
                elif drop_pct <= -25.0:
                    tags = [t for t in tags if "聰明錢落飛" not in str(t) and "資金冷卻回飛" not in str(t)]
                    tags.append(f"❄️ 資金冷卻回飛 (+{int(abs(drop_pct))}%)")
            else:
                # 若過往標籤同時混有落飛與回飛，或舊賠率被污染，強制清理矛盾回飛標籤
                if any("聰明錢落飛" in str(t) for t in tags) and any("資金冷卻回飛" in str(t) for t in tags):
                    tags = [t for t in tags if "資金冷卻回飛" not in str(t)]

            p_actual = pla_mkt_probs_300[i]
            p_harville = harville_pla_probs[i]
            if win_odd and win_odd >= 7.0 and pla_odd and pla_odd > 1.0 and p_harville > 0:
                if (p_actual / p_harville) >= 1.25 and not any("大戶位置鎖定" in str(t) for t in tags):
                    tags.append("🎯 大戶位置鎖定 (假冷真熱)")

            # 使用未摻入市場賠率水位的 adj_raw_probs 計算 EV (85% 基本面 + 15% 綜合)，防止雙重計算 (Double Counting)
            p_ev_model = (0.85 * adj_raw_probs[i] + 0.15 * final_probs[i]) / 100.0
            p_model = final_probs[i] / 100.0
            ev_pct = 0.0
            is_val = False
            if win_odd and win_odd > 1.0:
                ev = (p_ev_model * win_odd) - 1.0
                ev_pct = round(ev * 100.0, 1)
                if ev >= 0.08:
                    if win_odd <= 25.0 or p_ev_model >= 0.04:
                        is_val = True
                        b = win_odd - 1.0
                        k = round(min(5.0, max(0.5, (ev / (4.0 * b)) * 100.0)), 1)
                        tags.append(f"💎 正期望值 EV: +{ev_pct}%")
                        tags.append(f"📊 建議注碼: 1/4 Kelly ({k}%)")

            if pla_odd:
                tags.append(f"位置賠率: {pla_odd}倍")

            safe_ev = round(max(-99.0, min(999.0, ev_pct)), 1)
            safe_odd = round(min(999.0, max(1.0, win_odd)), 1) if win_odd and win_odd > 1.0 else None
            safe_prob = round(float(min(100.0, max(0.0, final_probs[i]))), 2)

            updated_payload.append({
                "race_id": race_id,
                "horse_no": h_no,
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": h["jockey"],
                "weight": h.get("weight", 125),
                "win_probability": safe_prob,
                "predicted_rank": h.get("predicted_rank", 99),
                "is_value_bet": is_val or h.get("predicted_rank", 99) <= 3,
                "gear": h["gear"],
                "rating": h["rating"],
                "smart_tags": list(set(tags)),
                "combo_synergy": safe_ev,
                "bet_strategy": h.get("bet_strategy", ""),
                "market_odds": safe_odd,
                "rest_days": h.get("rest_days") if h.get("rest_days") is not None else 21,
                "fast_gallop_count": h.get("fast_gallop_count") if h.get("fast_gallop_count") else 3
            })

        updated_payload.sort(key=lambda x: x["win_probability"], reverse=True)
        for r_idx, item in enumerate(updated_payload, 1):
            item["predicted_rank"] = r_idx
            has_smart = any("聰明錢落飛" in str(t) or "大戶位置鎖定" in str(t) for t in item["smart_tags"])
            if r_idx <= 4 and item["combo_synergy"] >= 8.0 and has_smart:
                item["bet_strategy"] = f"👑 終極黃金狙擊 (EV +{item['combo_synergy']}%)"
            elif r_idx == 1:
                item["bet_strategy"] = "🎯 獨贏首選 / 實力馬膽"
            elif r_idx == 2:
                item["bet_strategy"] = "⚡ 次選主力 / 黃金走位"
            elif r_idx <= 4:
                item["bet_strategy"] = "🛡️ 連贏配腳 / 高爆發冷門"
            elif item["is_value_bet"]:
                item["bet_strategy"] = f"💎 價值突擊 (EV +{item['combo_synergy']}%)"
            else:
                item["bet_strategy"] = ""

        sb.table("race_predictions").delete().eq("race_id", race_id).execute()
        clean_insert = [{k: v for k, v in row.items() if k != "place_odds"} for row in updated_payload]
        try:
            sb.table("race_predictions").insert(clean_insert).execute()
        except Exception as e:
            err_s = str(e)
            for opt_col in ["fast_gallop_count", "rest_days", "market_odds", "weight"]:
                if opt_col in err_s:
                    clean_insert = [{k: v for k, v in row.items() if k != opt_col} for row in clean_insert]
            sb.table("race_predictions").insert(clean_insert).execute()
        
        top1 = updated_payload[0]
        total_updated_horses += len(updated_payload)
        print(f"  ✓ 第 {race_no} 場同步完成 (涵蓋 {len(updated_payload)} 匹馬，首選: {top1['horse_no']}號 {top1['horse_name']}, 賠率: {top1['market_odds']}, 勝率: {top1['win_probability']}%)")

    global _SHARED_DRIVER
    if _SHARED_DRIVER:
        try: _SHARED_DRIVER.quit()
        except Exception: pass
        _SHARED_DRIVER = None

    print(f"\n🎉 同步完畢！共處理 {total_updated_horses} 匹馬資料！")

if __name__ == "__main__":
    sync_live_odds_and_ev()
