import os
import re
import json
import time
import requests
import numpy as np
from bs4 import BeautifulSoup
from supabase import create_client

# ==============================================================================
# 🏇 香港賽馬 AI：專業評馬人「六維核心架構」全息量化預測模型
# 🎯 整合香港賽馬六大專業基石：
#    1. 往績近況與班次途程 (25% 權重): 
#       - 班次升降(降班大優勢/升班挑戰)
#       - 途程對應(縮程爆發 1600➔1400 / 增程試準 1200➔1600 / 原程續戰)
#       - 6次近績走勢與班頂實力錨定
#    2. 跑道途程檔位官方統計 (20% 權重): 
#       - 2024至今沙田A賽道官方各途程勝率矩陣 (1000m直路外欄 vs 1200m/1600m轉彎內欄)
#    3. 騎練合作與負磅/更替 (20% 權重): 
#       - 騎練配搭默契度
#       - 騎師更換(換強配大師傅/換見習生實質減磅)
#       - 負磅增減權行(相比上仗增磅/減磅幅度)
#    4. 晨操數據與試閘評語 (15% 權重): 
#       - 試閘名次(勝出/前三)、正選騎師親操、賽前快跳課數
#    5. 配備變更殺機信號 (10% 權重): 
#       - 首次佩戴眼罩(B1/V1/PC1)、重戴(B2)、首次舌帶(TT1)
#    6. 排位體重增減分析 (10% 權重): 
#       - 壯身成長(+5至+15磅) vs 肥態未收(>+25磅) vs 體力透支(<-18磅)
#    + 15% 理性市場定價錨定: 85% 純專業數據 + 15% 賠率防瘋狂錨定，絕不離地！
# ==============================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rxmkohhgznfcnhdqegwq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ4bWtvaGhnem5mY25oZHFlZ3dxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MDQ5OTk2OCwiZXhwIjoyMTA2MDc1OTY4fQ.QUqbXyvQuVuulKbiaI0jC20aUw21l1vd4pjXWEryjuI")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.on.cc/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

def safe_db_op(op_func, max_retries=4):
    for attempt in range(1, max_retries + 1):
        try:
            return op_func()
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1.5 * attempt)
            else:
                print(f"Supabase 寫入異常重試失敗: {e}")
                raise e

# 🌟 跑道途程檔位官方統計勝率矩陣 (2024至今沙田 A 跑道)
def get_shatin_a_draw_score(distance, draw):
    if distance == 1000:
        if draw >= 10: return +0.20, ["🚀 直路看台外欄利位"]
        elif draw >= 7: return +0.08, []
        elif draw >= 4: return -0.05, []
        else: return -0.20, ["⚠️ 直路內欄吃風劣勢"]
    elif distance == 1200:
        if 2 <= draw <= 5: return +0.25, ["🎯 短途黃金內欄"]
        elif draw == 1: return +0.15, ["🎯 1檔貼欄好位"]
        elif 6 <= draw <= 8: return +0.05, []
        elif 9 <= draw <= 10: return -0.10, []
        else: return -0.25, ["⚠️ 大外檔轉彎蝕位"]
    elif distance == 1400:
        if 1 <= draw <= 6: return +0.15, ["🎯 中內檔穩定佔先"]
        elif 7 <= draw <= 9: return 0.00, []
        else: return -0.15, []
    elif distance == 1600:
        if 3 <= draw <= 6: return +0.25, ["🎯 一哩出閘最佳順暢位"]
        elif 1 <= draw <= 2: return +0.08, ["貼欄但防關門"]
        elif 7 <= draw <= 9: return -0.05, []
        else: return -0.20, ["⚠️ 一哩急彎外疊大蝕"]
    else:
        if 1 <= draw <= 4: return +0.20, ["🎯 長途貼欄省體力"]
        elif 5 <= draw <= 8: return +0.05, []
        else: return -0.18, []

# 🌟 晨操與試閘動態狀態解析器
def evaluate_trackwork(text, jockey_name):
    if not text: return 0.0, []
    clean_j = re.sub(r"\s*\(.*?\)", "", jockey_name).strip()
    score = 0.0
    tags = []

    trials = re.findall(r"第\d+組\d+\s+.*?[草地|全天候|泥地]\s*(\d+)/(\d+)\s*\((.*?)\)", text)
    for rank_str, total_str, j_rider in trials:
        rank = int(rank_str)
        if rank == 1:
            score += 0.20
            tags.append("🔥 晨操試閘第1名")
        elif rank <= 3:
            score += 0.12
            tags.append("⭐ 晨操試閘前三名")
        if clean_j and clean_j in j_rider:
            score += 0.10
            tags.append("🏇 騎師親自試閘")

    gallops = re.findall(r"(\d{2}/\d{2}):\s*.*?(?:沙田|從化).*?(\d{2}\.\d)\s*\(.*?\)\s*\((.*?)\)", text)
    if len(gallops) >= 3:
        score += 0.15
        tags.append("💪 賽前操足(3課+快跳)")
    elif len(gallops) >= 1:
        score += 0.08
        tags.append("✨ 正常快跳備戰")

    return score, list(set(tags))

def fetch_trackwork_text(date_hkjc, venue, race_no):
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalTrackwork.aspx?RaceDate={date_hkjc}&Racecourse={venue}&RaceNo={race_no}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""

# 🌟 配備變更分析器
def score_gear(gear_str):
    score = 0.0
    tags = []
    if not gear_str or gear_str == "-":
        return 0.0, tags
    g = gear_str.upper()
    if re.search(r"B1|V1|PC1|P1", g):
        score += 0.20
        tags.append("👓 首次眼罩 (動殺機變革)")
    elif re.search(r"B2|V2", g):
        score += 0.10
        tags.append("👓 重戴眼罩 (刺激走勢)")
    elif re.search(r"\bB\b|\bV\b|\bPC\b", g):
        score += 0.05
        tags.append("👓 配戴眼罩")

    if "TT1" in g or "XB1" in g:
        score += 0.12
        tags.append("👅 首次舌帶 (呼吸暢順)")
    elif "TT" in g:
        score += 0.05
        tags.append("👅 繫舌帶")
    return score, tags

# 🌟 排位體重增減分析器
def score_body_weight(wt_diff_val):
    score = 0.0
    tags = []
    if wt_diff_val is None:
        return 0.0, tags
    diff = wt_diff_val
    if 5 <= diff <= 15:
        score += 0.12
        tags.append(f"💪 體力壯身 (+{diff}磅)")
    elif -8 <= diff <= 4:
        score += 0.05
        tags.append(f"⚖️ 體重平穩 ({diff:+d}磅)")
    elif diff >= 25:
        score -= 0.15
        tags.append(f"⚠️ 體重暴增 (+{diff}磅，肥態未收)")
    elif diff <= -18:
        score -= 0.18
        tags.append(f"⚠️ 體重驟降 ({diff}磅，體力透支)")
    return score, tags

# 🌟 往績近況與上仗賽事對比分析器 (班次、途程、負磅、騎師變更)
def evaluate_form_and_transition(horse_code, curr_meta, curr_horse, form_str, avg_rating, sb_client):
    score = 0.0
    tags = []
    rating = curr_horse["rating"]

    # 1. 6次近績走勢分析
    if form_str and form_str != "-":
        runs = [int(r.strip()) for r in form_str.split("/") if r.strip().isdigit()]
        if runs:
            last_run = runs[0]
            if last_run == 1:
                score += 0.25
                tags.append("👑 上仗勝出頭馬")
            elif last_run <= 3:
                score += 0.16
                tags.append("🥈 上仗入位前三")
            elif last_run <= 5:
                score += 0.06
                tags.append("📈 上仗跑近前五")
            elif last_run >= 10:
                score -= 0.10
                tags.append("⚠️ 上仗大敗")

            win_count = sum(1 for r in runs if r == 1)
            if win_count >= 2:
                score += 0.12
                tags.append("🔥 近期2捷以上")
    else:
        tags.append("🆕 初出新馬")

    # 2. 班頂實力錨定
    if rating >= avg_rating + 5:
        score += 0.15
        tags.append("⭐ 班頂高分實力馬")

    # 3. 跨場上仗數據比對 (從 Supabase 讀取上仗歷史賽果)
    try:
        if horse_code and sb_client:
            res = sb_client.table("race_results").select(
                "distance, actual_weight, place_num, jockey, race_class"
            ).eq("horse_code", horse_code).order("race_date", desc=True).limit(1).execute()

            if res.data and len(res.data) > 0:
                last = res.data[0]
                last_d = last.get("distance")
                curr_d = curr_meta.get("distance", 1200)

                # 途程對應分析
                if last_d:
                    if last_d == curr_d:
                        score += 0.12
                        tags.append(f"🎯 原程續戰 ({curr_d}米)")
                    elif last_d > curr_d:
                        score += 0.15
                        tags.append(f"⚡ 縮程出擊 ({last_d}米 ➔ {curr_d}米)")
                    else:
                        if last.get("place_num", 10) <= 4:
                            score += 0.12
                            tags.append(f"🚀 增程試準 ({last_d}米 ➔ {curr_d}米)")
                        else:
                            score -= 0.05
                            tags.append(f"⚠️ 初試增程 ({curr_d}米)")

                # 負磅變更分析
                last_w = last.get("actual_weight")
                curr_w = float(curr_horse.get("weight", 125))
                if last_w:
                    w_delta = curr_w - float(last_w)
                    if w_delta <= -5:
                        score += 0.18
                        tags.append(f"🪶 相比上仗大幅減磅 ({w_delta:.0f}磅)")
                    elif w_delta <= -2:
                        score += 0.08
                        tags.append(f"🪶 相比上仗減磅 ({w_delta:.0f}磅)")
                    elif w_delta >= +5:
                        score -= 0.12
                        tags.append(f"⚖️ 相比上仗加磅 (+{w_delta:.0f}磅)")

                # 騎師更換分析
                last_j = re.sub(r"\s*\(.*?\)", "", last.get("jockey", "")).strip()
                curr_j = re.sub(r"\s*\(.*?\)", "", curr_horse.get("jockey", "")).strip()
                if last_j and curr_j and last_j != curr_j:
                    if curr_j in ["潘頓", "艾兆禮", "何澤堯", "布文", "巴度", "麥道朗"]:
                        score += 0.15
                        tags.append(f"⭐ 換強配大師傅 ({curr_j})")
                    elif re.search(r"\(-(\d+)\)", curr_horse.get("jockey", "")):
                        claim = re.search(r"\(-(\d+)\)", curr_horse.get("jockey", "")).group(1)
                        score += 0.12
                        tags.append(f"🪶 換見習生實質減磅 (-{claim}磅)")
                    else:
                        score += 0.05
                        tags.append(f"🔄 易配出擊 ({curr_j})")

                # 班次升降分析
                last_c = last.get("race_class", "")
                curr_c = curr_meta.get("race_class", "")
                if ("五班" in curr_c and "四班" in last_c) or ("四班" in curr_c and "三班" in last_c):
                    score += 0.22
                    tags.append("👑 降班出擊 (級數壓倒)")
                elif ("四班" in curr_c and "五班" in last_c) or ("三班" in curr_c and "四班" in last_c):
                    score -= 0.10
                    tags.append("⚠️ 升班挑戰")
    except Exception:
        pass

    return score, list(set(tags))

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
                v_name = m_cn.group(4)
                venue = "ST" if "沙田" in v_name else "HV"
                return f"{y}-{m:02d}-{d:02d}", f"{y}/{m:02d}/{d:02d}", venue
    except Exception as e:
        print(f"自動探測賽期連線警示: {e}")

    return "2026-10-01", "2026/10/01", "ST"

def fetch_live_odds(race_no):
    odds_map = {}
    url_oncc = f"https://racing.on.cc/racing/rat/current/rjratb{race_no:04d}x0.html"
    try:
        r = requests.get(url_oncc, headers=HEADERS, timeout=6)
        if r.status_code == 200 and r.text:
            r.encoding = "big5"
            soup = BeautifulSoup(r.text, "html.parser")
            for tr in soup.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if tds and tds[0].isdigit():
                    h_no = int(tds[0])
                    nums = [float(x) for x in tds[2:] if re.match(r'^\d+(\.\d+)?$', x)]
                    if nums:
                        win_odd = nums[-2] if (len(nums) >= 2 and len(nums) % 2 == 0) else nums[-1]
                        if 1.0 <= win_odd <= 999.0:
                            odds_map[h_no] = win_odd
            if odds_map:
                return odds_map
    except Exception:
        pass
    return odds_map

def fetch_race_horses(date_hkjc, venue, race_no):
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={date_hkjc}&Racecourse={venue}&RaceNo={race_no}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200: return None, []
    except Exception as e:
        print(f"排位表連線異常 R{race_no}: {e}")
        return None, []

    soup = BeautifulSoup(r.text, "html.parser")
    race_text = soup.get_text()

    if "沒有相關賽事" in race_text or "賽事取消" in race_text:
        return None, []

    dist_m = re.search(r"(\d{3,4})\s*米", race_text)
    distance = int(dist_m.group(1)) if dist_m else 1200
    track = "草地" if "草地" in race_text else ("全天候跑道" if "全天候" in race_text else "草地")

    course_m = re.search(r'\"([A-C\+3]+)\"\s*賽道', race_text)
    course = course_m.group(1) if course_m else None

    race_class = "第四班"
    for c in ("第一班", "第二班", "第三班", "第四班", "第五班", "三級賽", "二級賽", "一級賽"):
        if c in race_text: race_class = c; break

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
            elif "評分" in h and "+/-" not in h and "rating" not in h_idx:
                h_idx["rating"] = i
            elif "配備" in h and "gear" not in h_idx:
                h_idx["gear"] = i
            elif ("排位體重" in h or "體重" in h) and "+/-" in h and "weight_diff" not in h_idx:
                h_idx["weight_diff"] = i
            elif "6次近績" in h or "近績" in h:
                h_idx["form"] = i

        if "horse_no" not in h_idx or "horse_name" not in h_idx: continue

        for tr in tb.find_all("tr")[1:]:
            tds = [td.text.strip() for td in tr.find_all(["td", "th"])]
            if len(tds) <= h_idx["horse_no"]: continue
            val_no = tds[h_idx["horse_no"]]
            if not val_no.isdigit(): continue
            h_no = int(val_no)

            raw_name = tds[h_idx["horse_name"]]
            clean_name = re.sub(r"\s*\(.*?\)", "", raw_name).strip()
            h_code = ""
            cm = re.search(r"\(([A-Z0-9]+)\)", raw_name)
            if cm: h_code = cm.group(1)
            elif "horse_code" in h_idx and len(tds) > h_idx["horse_code"]:
                h_code = tds[h_idx["horse_code"]].replace("(", "").replace(")", "").strip()

            wt = 120
            if "weight" in h_idx and len(tds) > h_idx["weight"]:
                w_m = re.search(r"\d+", tds[h_idx["weight"]])
                if w_m: wt = int(w_m.group(0))

            jk = tds[h_idx["jockey"]] if "jockey" in h_idx and len(tds) > h_idx["jockey"] else ""
            dr = 7
            if "draw" in h_idx and len(tds) > h_idx["draw"]:
                d_m = re.search(r"\d+", tds[h_idx["draw"]])
                if d_m: dr = int(d_m.group(0))

            tr_name = tds[h_idx["trainer"]] if "trainer" in h_idx and len(tds) > h_idx["trainer"] else ""
            rt = 40
            if "rating" in h_idx and len(tds) > h_idx["rating"]:
                r_m = re.search(r"\d+", tds[h_idx["rating"]])
                if r_m: rt = int(r_m.group(0))

            gr = tds[h_idx["gear"]] if "gear" in h_idx and len(tds) > h_idx["gear"] else "-"
            form_val = tds[h_idx["form"]] if "form" in h_idx and len(tds) > h_idx["form"] else "-"

            wd_val = None
            if "weight_diff" in h_idx and len(tds) > h_idx["weight_diff"]:
                wd_match = re.search(r"[-+]?\d+", tds[h_idx["weight_diff"]])
                if wd_match: wd_val = int(wd_match.group(0))

            if h_no not in runners_map:
                runners_map[h_no] = {
                    "horse_no": h_no, "horse_code": h_code, "horse_name": clean_name,
                    "weight": wt, "jockey": jk, "draw": dr, "trainer": tr_name,
                    "rating": rt, "gear": gr, "form": form_val, "weight_diff": wd_val
                }

    horses = [runners_map[k] for k in sorted(runners_map.keys())]
    meta = {
        "venue": venue, "distance": distance, "track_type": track,
        "course": course, "race_class": race_class
    }
    return meta, horses

def run_upcoming():
    print("=== 🏇 香港賽馬 AI：專業評馬人「六維核心架構」全息量化預測系統 ===")
    target_date, date_hkjc, venue = detect_upcoming_meeting()
    print(f"賽事日期: {target_date} ({venue}) | 賽道: A跑道")

    total_races = 0
    sb_master = create_client(SUPABASE_URL, SUPABASE_KEY)

    for race_no in range(1, 12):
        meta, horses = fetch_race_horses(date_hkjc, venue, race_no)
        if not meta or not horses:
            break

        total_races += 1
        race_id = f"{target_date.replace('-', '')}_{venue}_{race_no:02d}"

        odds_map = fetch_live_odds(race_no)
        tw_html = fetch_trackwork_text(date_hkjc, venue, race_no)

        safe_db_op(lambda: create_client(SUPABASE_URL, SUPABASE_KEY).table("races").upsert({
            "race_id": race_id, "race_date": target_date,
            "venue": meta["venue"], "race_no": race_no, "distance": meta["distance"],
            "track_type": meta["track_type"], "course": meta["course"],
            "race_class": meta["race_class"]
        }).execute())

        avg_r = sum(float(h["rating"]) for h in horses) / len(horses) if horses else 40.0
        avg_w = sum(float(h["weight"]) for h in horses) / len(horses) if horses else 122.0
        dist = meta["distance"]

        scores = []
        tags_meta = {}
        for h in horses:
            h_no = h["horse_no"]
            h_tags = []

            # 🌟 維度 1: 往績近況、途程對應與班次級數 (25% 權重)
            s_form_trans, f_tags = evaluate_form_and_transition(
                h["horse_code"], meta, h, h["form"], avg_r, sb_master
            )
            r_scale = (float(h["rating"]) - avg_r) / 7.0
            pillar_1 = r_scale * 0.55 + s_form_trans * 0.45
            h_tags.extend(f_tags)

            # 🌟 維度 2: 跑道途程檔位官方統計勝率 (20% 權重)
            d_score, d_tags = get_shatin_a_draw_score(dist, h["draw"])
            pillar_2 = d_score
            h_tags.extend(d_tags)

            # 🌟 維度 3: 騎練合作與負磅/減磅 (20% 權重)
            wt = float(h["weight"])
            if wt >= 134:
                w_score = -0.22
                h_tags.append("⚖️ 頂磅考驗")
            elif 124 <= wt <= 129:
                w_score = +0.18
                h_tags.append("⚡ 今日黃金負磅區")
            elif wt <= 123:
                w_score = +0.12
                h_tags.append("🪶 輕磅飛馳")
            else:
                w_score = 0.0

            m_claim = re.search(r"\(-(\d+)\)", h["jockey"])
            claim_bonus = 0.12 if m_claim else 0.0
            pillar_3 = w_score * 0.70 + claim_bonus * 0.30

            # 🌟 維度 4: 晨操數據與試閘評語 (15% 權重)
            tw_score, tw_tags = evaluate_trackwork(tw_html, h["jockey"])
            pillar_4 = tw_score
            h_tags.extend(tw_tags)

            # 🌟 維度 5: 配備變更殺機信號 (10% 權重)
            g_score, g_tags = score_gear(h["gear"])
            pillar_5 = g_score
            h_tags.extend(g_tags)

            # 🌟 維度 6: 排位體重增減分析 (10% 權重)
            wt_score, wt_tags = score_body_weight(h["weight_diff"])
            pillar_6 = wt_score
            h_tags.extend(wt_tags)

            # 綜合六大專業維度
            total_feature = (
                (pillar_1 * 0.25) + 
                (pillar_2 * 0.20) + 
                (pillar_3 * 0.20) + 
                (pillar_4 * 0.15) + 
                (pillar_5 * 0.10) + 
                (pillar_6 * 0.10)
            )
            scores.append(total_feature)
            tags_meta[h_no] = list(set(h_tags))

        # 計算純專業實力勝率
        scores = np.array(scores)
        exp_s = np.exp(scores * 2.2)
        raw_probs = (exp_s / exp_s.sum()) * 100.0

        # 🌟 15% 理性市場定價錨定 (85% 純專業數據 + 15% 溫和賠率錨定，杜絕離地)
        has_odds = any(h["horse_no"] in odds_map and odds_map[h["horse_no"]] > 1.0 for h in horses)
        if has_odds:
            mkt_implied = np.array([1.0 / odds_map.get(h["horse_no"], 20.0) for h in horses])
            mkt_probs = (mkt_implied / mkt_implied.sum()) * 100.0
            final_probs = 0.15 * mkt_probs + 0.85 * raw_probs
        else:
            final_probs = raw_probs

        scored = []
        for i, h in enumerate(horses):
            h_no = h["horse_no"]
            odds = odds_map.get(h_no)
            tags = tags_meta.get(h_no, [])

            jockey_trainer_str = f"{h['jockey']} / {h['trainer']}" if h.get("trainer") else h["jockey"]

            is_val = False
            if odds and odds >= 6.0 and final_probs[i] >= 9.5:
                is_val = True
                tags.append("💎 今日高爆發冷馬")

            scored.append({
                "race_id": race_id,
                "horse_no": h_no,
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": jockey_trainer_str,
                "weight": h["weight"],
                "win_probability": round(float(final_probs[i]), 2),
                "gear": h["gear"],
                "rating": h["rating"],
                "smart_tags": tags,
                "combo_synergy": 0.0,
                "market_odds": odds,
                "is_value_bet": is_val
            })

        scored.sort(key=lambda x: x["win_probability"], reverse=True)
        final_payload = []
        for rank, item in enumerate(scored, 1):
            if rank == 1:
                strat = "🎯 獨贏首選 / 實力馬膽"
            elif rank == 2:
                strat = "⚡ 次選主力 / 黃金走位"
            elif rank <= 4:
                strat = "🛡️ 連贏配腳 / 高爆發冷門"
            elif item["is_value_bet"]:
                strat = "💎 價值突擊"
            else:
                strat = ""

            final_payload.append({
                "race_id": item["race_id"],
                "horse_no": item["horse_no"],
                "horse_code": item["horse_code"],
                "horse_name": item["horse_name"],
                "draw": item["draw"],
                "jockey": item["jockey"],
                "win_probability": item["win_probability"],
                "predicted_rank": rank,
                "is_value_bet": item["is_value_bet"] or (rank <= 3),
                "gear": item["gear"],
                "rating": item["rating"],
                "smart_tags": item["smart_tags"],
                "combo_synergy": item["combo_synergy"],
                "bet_strategy": strat,
                "market_odds": item["market_odds"]
            })

        def _write_preds():
            c = create_client(SUPABASE_URL, SUPABASE_KEY)
            c.table("race_predictions").delete().eq("race_id", race_id).execute()
            c.table("race_predictions").insert(final_payload).execute()
        safe_db_op(_write_preds)

        top_h = scored[0]
        odds_count = sum(1 for p in final_payload if p.get("market_odds") is not None)
        print(f"  ✓ 第 {race_no} 場完成 ({meta['distance']}米, 出賽: {len(horses)} 匹, 首選: {top_h['horse_no']}號 {top_h['horse_name']} [{top_h['draw']}檔/{top_h['weight']}磅], 勝率:{top_h['win_probability']}%, 賠率:{top_h['market_odds']})")

    print(f"\n🎉 成功！已完成專業評馬人六維綜合預測並全部寫入 Supabase！")

if __name__ == "__main__":
    run_upcoming()
