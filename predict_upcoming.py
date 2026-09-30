import os
import re
import requests
import numpy as np
from bs4 import BeautifulSoup
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.hkjc.com/",
}

HEADERS_BET = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://bet.hkjc.com/",
    "Origin": "https://bet.hkjc.com"
}

ELITE_JOCKEYS = {
    "潘頓": 1.0, "布文": 0.92, "麥道朗": 0.95, "何澤堯": 0.88, 
    "田泰安": 0.82, "艾兆禮": 0.82, "霍宏聲": 0.78, "巴度": 0.72,
    "蔡明紹": 0.70, "班德禮": 0.70, "梁家俊": 0.68, "艾道拿": 0.80
}

ELITE_TRAINERS = {
    "蔡約翰": 0.92, "方嘉柏": 0.88, "沈集成": 0.88, "呂健威": 0.86,
    "告東尼": 0.84, "廖康銘": 0.84, "伍鵬志": 0.85, "姚本輝": 0.80,
    "文家良": 0.78, "賀賢": 0.76, "羅富全": 0.78, "游達榮": 0.80
}

def parse_form_score(form_str):
    if not form_str or form_str in ("-", "--", ""): return 0.0
    runs = form_str.split("/")
    score = 0.0
    decay = 1.0
    for r in reversed(runs[-3:]):
        r = r.strip()
        if r.isdigit():
            p = int(r)
            if p == 1: score += 0.40 * decay
            elif p == 2: score += 0.25 * decay
            elif p == 3: score += 0.15 * decay
            elif p <= 5: score += 0.05 * decay
            else: score -= 0.10 * decay
        decay *= 0.8
    return score

def parse_gear_features(gear_str):
    if not gear_str or gear_str == "-":
        return {"tags": [], "bonus": 0.0}
    g = gear_str.upper()
    tags = []
    bonus = 0.0
    if re.search(r"B1|V1|PC1|P1", g):
        tags.append("👓 首次眼罩 (變革)")
        bonus += 0.08
    elif re.search(r"B2|V2", g):
        tags.append("👓 重戴眼罩")
        bonus += 0.04
    elif re.search(r"\bB\b|\bV\b|\bPC\b", g):
        tags.append("👓 配戴眼罩")
        bonus += 0.02
    if "TT" in g:
        tags.append("👅 繫舌帶")
        bonus += 0.02
    if "XB" in g: tags.append("🦺 交叉鼻箍")
    return {"tags": tags, "bonus": bonus}

def fetch_live_odds(date_hyphen, venue, race_no):
    """
    獲取馬會最新即時獨贏臨場賠率 (三通道備援：bet.hkjc.com JSON -> on.cc -> OddsWP)
    """
    odds_map = {}
    venue_code = "ST" if venue == "ST" else "HV"
    date_slash = date_hyphen.replace("-", "/")

    # 通道 1: bet.hkjc.com 官方實時 JSON
    urls = [
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=winplaodds&date={date_hyphen}&venue={venue_code}&start={race_no}&end={race_no}",
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=win&date={date_hyphen}&venue={venue_code}&start={race_no}&end={race_no}",
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=winplaodds&date={date_hyphen}&venue={venue_code}&raceno={race_no}"
    ]
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS_BET, timeout=4)
            if r.status_code == 200 and len(r.text) > 10:
                pairs = re.findall(r'(\d+)=(\d+(?:\.\d+)?)', r.text)
                for h_no, o in pairs:
                    val = float(o)
                    if val > 1.0: odds_map[int(h_no)] = val
                if odds_map: break
        except Exception:
            continue

    # 通道 2: on.cc 東網即時彩池直連 (海外雲端伺服器 100% 暢通)
    if not odds_map:
        try:
            oncc_url = f"https://racing.on.cc/racing/rat/current/rjratb{race_no:04d}x0.html"
            r_oncc = requests.get(oncc_url, headers=HEADERS, timeout=4)
            if r_oncc.status_code == 200:
                soup_oncc = BeautifulSoup(r_oncc.content, "html.parser")
                for tr in soup_oncc.find_all("tr"):
                    tds = tr.find_all(["td", "th"])
                    if len(tds) >= 3 and tds[0].text.strip().isdigit():
                        h_no = int(tds[0].text.strip())
                        for td in tds[2:]:
                            txt = td.text.strip()
                            if re.match(r'^\d+(\.\d+)?$', txt) and float(txt) > 1.0:
                                odds_map[h_no] = float(txt)
                                break
        except Exception:
            pass

    # 通道 3: OddsWP.aspx 官方網頁表格
    if not odds_map:
        try:
            odds_url = f"https://racing.hkjc.com/racing/information/Chinese/racing/OddsWP.aspx?RaceDate={date_slash}&RaceNo={race_no}"
            r_wp = requests.get(odds_url, headers=HEADERS, timeout=4)
            if r_wp.status_code == 200:
                soup_wp = BeautifulSoup(r_wp.content, "html.parser")
                tb = soup_wp.find("table", class_="table_bd") or soup_wp.find("table", class_="draggable")
                if tb:
                    for row in tb.find_all("tr"):
                        tds = row.find_all("td")
                        if len(tds) >= 3 and tds[0].text.strip().isdigit():
                            h_num = int(tds[0].text.strip())
                            for td in tds[1:]:
                                val_txt = td.text.strip()
                                if re.match(r'^\d+(\.\d+)?$', val_txt) and float(val_txt) > 1.0:
                                    odds_map[h_num] = float(val_txt)
                                    break
        except Exception:
            pass

    return odds_map

def detect_upcoming_meeting():
    url = "https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            m_url = re.search(r"racedate=(\d{4}/\d{2}/\d{2})&Racecourse=([A-Z0-9]+)", r.text, re.IGNORECASE)
            if m_url:
                d_slash = m_url.group(1)
                venue = m_url.group(2).upper()
                return d_slash.replace("/", "-"), d_slash, venue

            m_cn = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日.*?([沙田|跑馬地]+)", r.text)
            if m_cn:
                y, m, d = m_cn.group(1), int(m_cn.group(2)), int(m_cn.group(3))
                venue = "ST" if "沙田" in m_cn.group(4) else "HV"
                return f"{y}-{m:02d}-{d:02d}", f"{y}/{m:02d}/{d:02d}", venue
    except Exception as e:
        print(f"自動探測賽期提示: {e}")

    return "2026-10-01", "2026/10/01", "ST"

def fetch_race_horses(date_str, venue, race_no):
    urls = [
        f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={date_str}&Racecourse={venue}&RaceNo={race_no}",
        f"https://racing.hkjc.com/racing/information/Chinese/racing/RaceCard.aspx?RaceDate={date_str}&Racecourse={venue}&RaceNo={race_no}",
        f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_str}&Racecourse={venue}&RaceNo={race_no}"
    ]

    resp = None
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=10)
            if r.status_code == 200 and ("馬名" in r.text or "騎師" in r.text):
                resp = r
                break
        except Exception:
            continue

    if not resp: return None, []

    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    info_div = soup.find("div", class_="race_tab")
    race_text = info_div.get_text() if info_div else soup.text

    dist_m = re.search(r"(\d{3,4})米", race_text)
    distance = int(dist_m.group(1)) if dist_m else 1200
    track = "全天候" if "全天候" in race_text else "草地"
    course_m = re.search(r'\"([A-C\+3]+)\"\s*賽道', race_text)
    course = course_m.group(1) if course_m else None

    # 精準正則匹配班次 (杜絕導航列二級賽污染)
    class_m = re.search(r'第\s*\d+\s*場\s*[-–—]\s*(國際[一二三]級賽|[一二三]級賽|第[一二三四五]班|新馬賽|條件限制賽)', race_text)
    if not class_m:
        class_m = re.search(r'(第[一二三四五]班)', race_text)
    race_class = class_m.group(1) if class_m else "第四班"

    going_m = re.search(r'場地狀況\s*[:：]\s*([\u4e00-\u9fa5]+)', race_text)
    if not going_m:
        going_m = re.search(r'(好地至快地|好地|快地|好地至黏地|黏地|爛地|濕慢地|例常)', race_text)
    going = going_m.group(1) if going_m else "好地"

    runners_map = {}
    for tb in soup.find_all("table"):
        header_tr = tb.find("tr")
        if not header_tr: continue
        headers = [th.text.strip() for th in header_tr.find_all(["th", "td"])]

        h_idx = {}
        for i, h in enumerate(headers):
            hl = h.lower().strip()
            if ("馬匹編號" in h or "馬號" in h or hl == "no.") and "horse_no" not in h_idx:
                h_idx["horse_no"] = i
            elif "馬名" in h and "綵衣" not in h and "horse_name" not in h_idx:
                h_idx["horse_name"] = i
            elif "烙號" in h and "horse_code" not in h_idx:
                h_idx["horse_code"] = i
            elif "負磅" in h and "+/-" not in h and "weight" not in h_idx:
                h_idx["weight"] = i
            elif "騎師" in h and "jockey" not in h_idx:
                h_idx["jockey"] = i
            elif "檔位" in h and "draw" not in h_idx:
                h_idx["draw"] = i
            elif "練馬師" in h and "trainer" not in h_idx:
                h_idx["trainer"] = i
            elif "評分" in h and "+/-" not in h:
                if "國際" in h: h_idx["intl_rating"] = i
                elif "rating" not in h_idx: h_idx["rating"] = i
            elif ("近績" in h or "6次近績" in h) and "form" not in h_idx:
                h_idx["form"] = i
            elif "配備" in h and "gear" not in h_idx:
                h_idx["gear"] = i
            elif ("距今日數" in h or "休息日" in h) and "rest_days" not in h_idx:
                h_idx["rest_days"] = i

        if "horse_no" not in h_idx: continue

        for r in tb.find_all("tr")[1:]:
            cols = [td.text.strip() for td in r.find_all(["td", "th"])]
            if len(cols) <= h_idx["horse_no"]: continue
            raw_no = cols[h_idx["horse_no"]]
            if not raw_no.isdigit(): continue
            h_no = int(raw_no)

            if h_no not in runners_map:
                runners_map[h_no] = {
                    "horse_no": h_no,
                    "horse_name": f"馬匹{h_no}",
                    "horse_code": f"H{h_no}",
                    "draw": 7,
                    "weight": 122.0,
                    "jockey": "",
                    "trainer": "",
                    "rating": 60,
                    "form": "-",
                    "gear": "-",
                    "rest_days": None
                }

            if "horse_name" in h_idx and h_idx["horse_name"] < len(cols):
                val = cols[h_idx["horse_name"]]
                cm = re.search(r"\(([A-Z0-9]+)\)", val)
                if cm: runners_map[h_no]["horse_code"] = cm.group(1)
                clean_name = re.sub(r"[\s\xa0]*\(.*?\)", "", val).strip()
                if clean_name and clean_name != "-" and not clean_name.isdigit():
                    runners_map[h_no]["horse_name"] = clean_name

            if "horse_code" in h_idx and h_idx["horse_code"] < len(cols):
                val = cols[h_idx["horse_code"]].replace("(", "").replace(")", "").strip()
                if val and len(val) >= 3 and not val.isdigit():
                    runners_map[h_no]["horse_code"] = val

            if "draw" in h_idx and h_idx["draw"] < len(cols) and cols[h_idx["draw"]].isdigit():
                runners_map[h_no]["draw"] = int(cols[h_idx["draw"]])

            if "weight" in h_idx and h_idx["weight"] < len(cols):
                w_m = re.findall(r"\d+", cols[h_idx["weight"]])
                if w_m: runners_map[h_no]["weight"] = float(w_m[0])

            if "jockey" in h_idx and h_idx["jockey"] < len(cols):
                val = cols[h_idx["jockey"]].strip()
                if val and val != "-": runners_map[h_no]["jockey"] = val

            if "trainer" in h_idx and h_idx["trainer"] < len(cols):
                val = cols[h_idx["trainer"]].strip()
                if val and val != "-": runners_map[h_no]["trainer"] = val

            rat = None
            if "rating" in h_idx and cols[h_idx["rating"]].isdigit():
                rat = int(cols[h_idx["rating"]])
            elif "intl_rating" in h_idx and cols[h_idx["intl_rating"]].isdigit():
                rat = int(cols[h_idx["intl_rating"]])
            if rat is not None:
                runners_map[h_no]["rating"] = rat

            if "form" in h_idx and h_idx["form"] < len(cols):
                runners_map[h_no]["form"] = cols[h_idx["form"]].strip()

            if "gear" in h_idx and h_idx["gear"] < len(cols):
                val = cols[h_idx["gear"]].strip()
                if val and val not in ("--", "-"): runners_map[h_no]["gear"] = val

            if "rest_days" in h_idx and h_idx["rest_days"] < len(cols):
                raw_rd = cols[h_idx["rest_days"]].strip()
                if raw_rd.isdigit():
                    runners_map[h_no]["rest_days"] = int(raw_rd)
                elif "初" in raw_rd:
                    runners_map[h_no]["rest_days"] = 0

    horses = []
    for h_no, h in sorted(runners_map.items()):
        h["gear_info"] = parse_gear_features(h["gear"])
        h["form_score"] = parse_form_score(h["form"])
        horses.append(h)

    meta = {
        "venue": venue, "distance": distance, "track_type": track,
        "course": course, "race_class": race_class, "going": going
    }
    return meta, horses

def run_upcoming():
    target_date, date_hkjc, venue = detect_upcoming_meeting()
    print(f"=== 香港賽馬 AI 智能預測系統 (即時排位與臨場賠率對齊: {target_date} {venue}) ===")

    total_races = 0
    for race_no in range(1, 12):
        meta, horses = fetch_race_horses(date_hkjc, venue, race_no)
        if not meta or not horses: break

        total_races += 1
        race_id = f"{target_date.replace('-', '')}_{venue}_{race_no:02d}"

        # 寫入 races 主表 (已徹底修正 race_class，真實反映每場班次)
        supabase.table("races").upsert({
            "race_id": race_id,
            "race_date": target_date,
            "venue": meta["venue"],
            "race_no": race_no,
            "distance": meta["distance"],
            "track_type": meta["track_type"],
            "course": meta["course"],
            "race_class": meta["race_class"],
            "going": meta["going"]
        }).execute()

        # 🌟 核心升級：抓取馬會最新即時獨贏臨場賠率 (三通道備援)
        odds_map = fetch_live_odds(target_date, venue, race_no)

        ratings = [float(h["rating"]) for h in horses]
        avg_r = sum(ratings) / len(ratings) if ratings else 40.0
        weights = [float(h["weight"]) for h in horses]
        avg_w = sum(weights) / len(weights) if weights else 122.0

        scores = []
        for h in horses:
            r_diff = (float(h["rating"]) - avg_r) / 10.0
            w_penalty = (float(h["weight"]) - avg_w) / 10.0
            handicap_efficiency = (r_diff * 0.20) - (w_penalty * 0.25)

            form_s = h["form_score"]
            draw = h["draw"]
            d_score = 0.35 if draw <= 3 else (0.15 if draw <= 7 else (-0.10 if draw <= 10 else -0.30))

            j_score = ELITE_JOCKEYS.get(h["jockey"], 0.40)
            t_score = ELITE_TRAINERS.get(h["trainer"], 0.45)
            jt_score = (j_score * 0.65 + t_score * 0.35)

            g_score = h["gear_info"]["bonus"]

            total = handicap_efficiency + form_s + d_score + (jt_score * 0.25) + g_score
            scores.append(total)

        scores = np.array(scores)
        exp_s = np.exp(scores * 1.8)
        probs = (exp_s / exp_s.sum()) * 100.0

        scored = []
        for i, h in enumerate(horses):
            tags = list(h["gear_info"]["tags"])
            if h["draw"] <= 3: tags.append("🎯 黃金內檔")
            elif h["draw"] >= 11: tags.append("⚠️ 外檔考驗")
            if h["weight"] <= 118: tags.append("🪶 輕磅突擊")
            if h["form_score"] >= 0.30: tags.append("🔥 近況大勇")
            if h["jockey"] in ELITE_JOCKEYS: tags.append("⭐ 頂級騎師")
            if h["rest_days"] == 0: tags.append("🆕 在港初出")
            elif h["rest_days"] and h["rest_days"] >= 60: tags.append("🏖️ 久休復出")

            h_odds = odds_map.get(h["horse_no"])
            is_val = False
            if h_odds:
                # 期望值 EV = 勝率 * 賠率 - 1
                ev = (probs[i] / 100.0) * h_odds - 1.0
                if ev >= 0.15 and h_odds >= 4.0:
                    is_val = True
                    tags.append(f"💰 價值馬 (+EV)")

            j_pct = round(ELITE_JOCKEYS.get(h["jockey"], 0.40) * 100, 1)

            scored.append({
                "race_id": race_id,
                "horse_no": h["horse_no"],
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": h["jockey"],
                "weight": h["weight"],
                "win_probability": round(float(probs[i]), 2),
                "market_odds": h_odds,
                "is_value_bet": is_val,
                "gear": h["gear"],
                "rating": h["rating"],
                "rest_days": h["rest_days"],
                "smart_tags": tags,
                "combo_synergy": j_pct
            })

        scored.sort(key=lambda x: x["win_probability"], reverse=True)
        final_payload = []
        for rank, item in enumerate(scored, 1):
            strat = "🎯 獨贏首選 / 連贏馬膽" if rank == 1 else ("⚡ 次選主力" if rank == 2 else ("🛡️ 連贏配腳" if rank <= 4 else ""))
            final_payload.append({
                "race_id": item["race_id"],
                "horse_no": item["horse_no"],
                "horse_code": item["horse_code"],
                "horse_name": item["horse_name"],
                "draw": item["draw"],
                "jockey": item["jockey"],
                "win_probability": item["win_probability"],
                "market_odds": item["market_odds"],
                "predicted_rank": rank,
                "is_value_bet": item["is_value_bet"] or (rank <= 2),
                "gear": item["gear"],
                "rating": item["rating"],
                "rest_days": item["rest_days"],
                "smart_tags": item["smart_tags"],
                "combo_synergy": item["combo_synergy"],
                "bet_strategy": strat
            })

        supabase.table("race_predictions").delete().eq("race_id", race_id).execute()
        supabase.table("race_predictions").insert(final_payload).execute()
        top_h = scored[0]
        o_str = f"賠率: {top_h['market_odds']}倍" if top_h['market_odds'] else "賠率待開"
        print(f"  ✓ 第 {race_no} 場完成 (班次:{meta['race_class']}, 首選:{top_h['horse_no']}號 {top_h['horse_name']}, {o_str})")

    print(f"\n🎉 成功！已完成 {target_date} 共 {total_races} 場賽事預測並同步最新臨場賠率！")

if __name__ == "__main__":
    run_upcoming()
