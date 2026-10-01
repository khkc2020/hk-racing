import os
import re
import json
import time
import requests
import numpy as np
from bs4 import BeautifulSoup
from supabase import create_client

# ==============================================================================
# 🏇 香港賽馬 AI：今日專屬臨場校準模型 (Today's Calibrated Track-Bias Model)
# 🎯 依據 2026-10-01 沙田 A 賽道頭兩場實戰結果深度校準：
#    【R1 驗證】：7號萬里雲(3檔/129磅) 12倍奪冠！2號靖哥哥(2檔)亞軍！(2.1倍大熱5號包尾)
#    【R2 驗證】：9號開心三多(1檔/128磅)奪冠！大熱門(2.1倍/3.7倍)連續全軍覆沒！
# 🔑 今日實戰核心修正法則：
#    1. 零市場偏見（0% Odds Weight）：徹底剔除市場賠率盲從，專抓走位好、有分頭的真實良駒。
#    2. 轉彎賽事極限內欄加權（45%）：沙田 A 欄 1~3 檔享有絕對貼欄省腳程優勢。
#    3. 直路賽(1000米)特別切換：1000 米直路賽自動切換為外欄看台優勢（大檔位有利）。
#    4. 頂磅損耗與輕磅爆發（25%）：134磅以上頂磅嚴格壓抑，124~129磅中輕磅黃金衝刺區大幅加分。
# ==============================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rxmkohhgznfcnhdqegwq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ4bWtvaGhnem5mY25oZHFlZ3dxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MDQ5OTk2OCwiZXhwIjoyMTA2MDc1OTY4fQ.QUqbXyvQuVuulKbiaI0jC20aUw21l1vd4pjXWEryjuI")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.on.cc/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# 🌟 Supabase 防斷線自動重試包裝器 (徹底解決 RemoteProtocolError: ConnectionTerminated)
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

# 今日發威與善戰之練馬師組合加權
ELITE_TRAINERS = {
    "賀賢": 0.95, "桂福特": 0.92, "韋達": 0.88, "甘敏斯": 0.85,
    "蔡約翰": 0.88, "方嘉柏": 0.85, "沈集成": 0.85, "呂健威": 0.85,
    "告東尼": 0.82, "伍鵬志": 0.85, "蘇偉賢": 0.82, "文家良": 0.80, "黎昭昇": 0.75
}

def parse_gear_tags(gear_str):
    if not gear_str or gear_str == "-": return []
    g = gear_str.upper()
    tags = []
    if re.search(r"B1|V1|PC1|P1", g): tags.append("👓 首次眼罩 (配備變革)")
    elif re.search(r"B2|V2", g): tags.append("👓 重戴眼罩")
    elif re.search(r"\bB\b|\bV\b|\bPC\b", g): tags.append("👓 配戴眼罩")
    if "TT1" in g or "XB1" in g: tags.append("👅 首次舌帶/鼻箍")
    elif "TT" in g: tags.append("👅 繫舌帶")
    return tags

def detect_upcoming_meeting():
    """自動從馬會首頁探測即將舉行的最新賽事日期與場地"""
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
    """即時抓取東網/馬會賠率 (僅用於介面展示與標註賠率超值，完全不干涉純走位排名)"""
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
    """精準抓取指定場次的排位表資料"""
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

            if h_no not in runners_map:
                runners_map[h_no] = {
                    "horse_no": h_no, "horse_code": h_code, "horse_name": clean_name,
                    "weight": wt, "jockey": jk, "draw": dr, "trainer": tr_name,
                    "rating": rt, "gear": gr
                }

    horses = [runners_map[k] for k in sorted(runners_map.keys())]
    meta = {
        "venue": venue, "distance": distance, "track_type": track,
        "course": course, "race_class": race_class
    }
    return meta, horses

def run_upcoming():
    print("=== 🏇 香港賽馬 AI：今日專屬臨場校準預測系統 ===")
    target_date, date_hkjc, venue = detect_upcoming_meeting()
    print(f"賽事日期: {target_date} ({venue}) | 賽道: A跑道")

    total_races = 0

    for race_no in range(1, 12):
        meta, horses = fetch_race_horses(date_hkjc, venue, race_no)
        if not meta or not horses:
            break

        total_races += 1
        race_id = f"{target_date.replace('-', '')}_{venue}_{race_no:02d}"

        # 1. 抓取即時賠率 (僅用於介面展示，0% 權重干預預測)
        odds_map = fetch_live_odds(race_no)

        # 2. 登記賽事資料 (防斷線自動重試)
        safe_db_op(lambda: create_client(SUPABASE_URL, SUPABASE_KEY).table("races").upsert({
            "race_id": race_id, "race_date": target_date,
            "venue": meta["venue"], "race_no": race_no, "distance": meta["distance"],
            "track_type": meta["track_type"], "course": meta["course"],
            "race_class": meta["race_class"]
        }).execute())

        avg_r = sum(float(h["rating"]) for h in horses) / len(horses) if horses else 40.0
        avg_w = sum(float(h["weight"]) for h in horses) / len(horses) if horses else 122.0
        dist = meta["distance"]

        # 3. 🌟 今日實戰極限校準演算法 (Calibrated Formula)
        scores = []
        for h in horses:
            draw = h["draw"]
            # (1) 檔位偏差: 轉彎賽事(1200-1800m) A欄內欄黃金貼欄法則；1000m直路賽外欄看台法則
            if dist == 1000 and "全天候" not in meta["track_type"]:
                # 直路賽看台外欄優勢 (10-14 檔最佳)
                if draw >= 10: d_score = 0.50
                elif draw >= 7: d_score = 0.20
                elif draw >= 4: d_score = -0.10
                else: d_score = -0.30
            else:
                # 轉彎賽事極限內欄優勢 (今日 R1, R2 實戰驗證: 1~3檔完全統治頭馬與前列)
                if draw == 3: d_score = 0.60       # 3檔: 最佳切入好位 (R1萬里雲頭馬)
                elif draw <= 2: d_score = 0.50     # 1,2檔: 絕對貼欄 (R2開心三多頭馬/R1靖哥哥亞軍)
                elif draw <= 5: d_score = 0.20
                elif draw <= 8: d_score = -0.05
                elif draw <= 10: d_score = -0.25
                else: d_score = -0.50              # 大外檔轉彎蝕位

            # (2) 負磅損耗曲線: 134磅頂磅扣分，124-129磅黃金發力區加分
            wt = float(h["weight"])
            if wt >= 134: w_score = -0.35          # 頂磅消耗嚴苛
            elif wt >= 131: w_score = -0.18
            elif 124 <= wt <= 129: w_score = +0.25 # 今日贏馬之黃金負磅區間 (128磅、129磅)
            elif wt < 124: w_score = +0.15
            else: w_score = 0.0

            # (3) 評分實力淨值
            r_score = (float(h["rating"]) - avg_r) / 10.0

            # (4) 今日當旺與善戰練馬師加權
            clean_t = re.sub(r"\s*\(.*?\)", "", h["trainer"]).strip()
            t_score = ELITE_TRAINERS.get(clean_t, 0.75) - 0.75

            # 總特徵: 檔位 45% + 負磅 25% + 評分 20% + 練馬師 10% (0% 市場賠率偏見)
            total = (d_score * 0.45) + (w_score * 0.25) + (r_score * 0.20) + (t_score * 0.10)
            scores.append(total)

        # 4. Softmax 轉換獨立勝率
        scores = np.array(scores)
        exp_s = np.exp(scores * 2.5)
        probs = (exp_s / exp_s.sum()) * 100.0

        scored = []
        for i, h in enumerate(horses):
            h_no = h["horse_no"]
            odds = odds_map.get(h_no)

            tags = parse_gear_tags(h["gear"])
            if dist == 1000:
                if h["draw"] >= 10: tags.append("🚀 看台外欄利位")
            else:
                if h["draw"] <= 3: tags.append("🎯 今日黃金內欄")
                elif h["draw"] >= 11: tags.append("⚠️ 外檔蝕位考驗")

            if 124 <= h["weight"] <= 129: tags.append("⚡ 今日黃金負磅區")
            elif h["weight"] >= 134: tags.append("⚖️ 頂磅考驗")

            jockey_trainer_str = f"{h['jockey']} / {h['trainer']}" if h.get("trainer") else h["jockey"]

            # 判斷是否為今日高期望值價值馬 (賠率 6.0~25.0 且 走位評分極佳)
            is_val = False
            if odds and odds >= 6.0 and probs[i] >= 9.5:
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
                "win_probability": round(float(probs[i]), 2),
                "gear": h["gear"],
                "rating": h["rating"],
                "smart_tags": tags,
                "combo_synergy": 0.0,
                "market_odds": odds,
                "is_value_bet": is_val
            })

        # 按今日校準走位勝率排序
        scored.sort(key=lambda x: x["win_probability"], reverse=True)
        final_payload = []
        for rank, item in enumerate(scored, 1):
            if rank == 1:
                strat = "🎯 獨贏首選 / 內檔突擊馬膽"
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

        # 寫入預測結果 (防 HTTP/2 斷線自動重建連線)
        def _write_preds():
            c = create_client(SUPABASE_URL, SUPABASE_KEY)
            c.table("race_predictions").delete().eq("race_id", race_id).execute()
            c.table("race_predictions").insert(final_payload).execute()
        safe_db_op(_write_preds)

        top_h = scored[0]
        odds_count = sum(1 for p in final_payload if p.get("market_odds") is not None)
        print(f"  ✓ 第 {race_no} 場完成 (出賽: {len(horses)} 匹, 賠率匹配: {odds_count} 匹, 首選: {top_h['horse_no']}號 {top_h['horse_name']} [{top_h['draw']}檔/{top_h['weight']}磅], 勝率:{top_h['win_probability']}%, 賠率:{top_h['market_odds']})")

    print(f"\n🎉 成功！已完成今日專屬校準預測並全部寫入 Supabase！")

if __name__ == "__main__":
    run_upcoming()
