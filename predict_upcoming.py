import os
import re
import json
import time
import requests
import numpy as np
from bs4 import BeautifulSoup
from supabase import create_client

# Supabase 連線設定（兼容本地與 GitHub Actions）
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rxmkohhgznfcnhdqegwq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ4bWtvaGhnem5mY25oZHFlZ3dxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MDQ5OTk2OCwiZXhwIjoyMTA2MDc1OTY4fQ.QUqbXyvQuVuulKbiaI0jC20aUw21l1vd4pjXWEryjuI")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://bet.hkjc.com/ch/racing/wp/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# 頂級騎練權重矩陣（長效統計，穩定不失真）
ELITE_JOCKEYS = {
    "潘頓": 1.0, "布文": 0.92, "麥道朗": 0.95, "何澤堯": 0.88, 
    "田泰安": 0.82, "艾兆禮": 0.82, "霍宏聲": 0.78, "巴度": 0.72,
    "蔡明紹": 0.70, "班德禮": 0.70, "梁家俊": 0.68
}

ELITE_TRAINERS = {
    "蔡約翰": 0.92, "方嘉柏": 0.88, "沈集成": 0.88, "呂健威": 0.86,
    "告東尼": 0.84, "廖康銘": 0.84, "伍鵬志": 0.85, "姚本輝": 0.80,
    "文家良": 0.78, "賀賢": 0.76, "羅富全": 0.78
}

def parse_gear_features(gear_str):
    """解析馬匹配備並標記關鍵訊號與調整權重"""
    if not gear_str or gear_str == "-":
        return {"tags": [], "bonus": 0.0}
    g = gear_str.upper()
    tags = []
    bonus = 0.0

    if re.search(r"B1|V1|PC1|P1", g):
        tags.append("👓 首次眼罩 (配備變革)")
        bonus += 0.08
    elif re.search(r"B2|V2", g):
        tags.append("👓 重戴眼罩")
        bonus += 0.04
    elif re.search(r"\bB\b|\bV\b|\bPC\b", g):
        tags.append("👓 配戴眼罩")
        bonus += 0.02
    elif re.search(r"B-|V-", g):
        tags.append("🔄 脫去眼罩")

    if "TT1" in g or "XB1" in g:
        tags.append("👅 首次舌帶/鼻箍")
        bonus += 0.05
    elif "TT" in g:
        tags.append("👅 繫舌帶")
        bonus += 0.02
    if "XB" in g:
        tags.append("🦺 交叉鼻箍")
    if "H1" in g or "E1" in g:
        tags.append("🎧 首次頭罩/耳塞")
    elif "H" in g:
        tags.append("🎧 戴頭罩")

    return {"tags": tags, "bonus": bonus}

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

def fetch_live_odds(date_str, venue, race_no):
    """
    🌟 專門直連馬會官方 eWin (https://bet.hkjc.com/ch/racing/wp/{date}/{venue}/{race_no})
    提取各馬匹「獨贏 (Win)」即時真實賠率
    """
    odds_map = {}

    # 通道 1：馬會官方 eWin 核心即時資料流 (返回格式: 1=6.4,2.3;2=6.7,2.7;...)
    data_stream_urls = [
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=winplaodds&date={date_str}&venue={venue}&start={race_no}&end={race_no}",
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=winplaodds&date={date_str}&venue={venue}&raceno={race_no}",
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=win&date={date_str}&venue={venue}&raceno={race_no}"
    ]

    for u in data_stream_urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=6)
            if r.status_code == 200 and r.text and "=" in r.text:
                tokens = r.text.replace("&", ";").split(";")
                for tok in tokens:
                    if "=" in tok:
                        parts = tok.split("=")
                        if len(parts) == 2 and parts[0].strip().isdigit():
                            h_no = int(parts[0].strip())
                            val_part = parts.split(",")[0].strip()
                            if re.match(r"^\d+(?:\.\d+)?$", val_part):
                                val = float(val_part)
                                if 1.0 <= val <= 999.0:
                                    odds_map[h_no] = val
                if odds_map:
                    return odds_map
        except Exception:
            pass

    # 通道 2：直連指定網頁 HTML 表格解析 (https://bet.hkjc.com/ch/racing/wp/{date}/{venue}/{race_no})
    web_urls = [
        f"https://bet.hkjc.com/ch/racing/wp/{date_str}/{venue}/{race_no}",
        f"https://bet.hkjc.com/en/racing/wp/{date_str}/{venue}/{race_no}",
        f"https://bet.hkjc.com/racing/pages/odds_wp.aspx?lang=ch&date={date_str}&venue={venue}&raceno={race_no}"
    ]

    for u in web_urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=8)
            if r.status_code == 200 and r.text:
                soup = BeautifulSoup(r.text, "html.parser")
                for table in soup.find_all("table"):
                    win_idx = None
                    for tr in table.find_all("tr"):
                        cells = [th.get_text(strip=True).lower() for th in tr.find_all(["th", "td"])]
                        for i, name in enumerate(cells):
                            if name in ("win", "獨贏", "獨贏賠率") and win_idx is None:
                                win_idx = i
                                break
                        if win_idx is not None:
                            break

                    if win_idx is not None:
                        for tr in table.find_all("tr"):
                            tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                            if len(tds) > win_idx and tds[0].isdigit():
                                h_no = int(tds[0])
                                val_str = tds[win_idx].replace("$", "").strip()
                                if re.match(r"^\d+(?:\.\d+)?$", val_str):
                                    val = float(val_str)
                                    if 1.0 <= val <= 999.0:
                                        odds_map[h_no] = val
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
    for c in ("第一班", "第二班", "第三班", "第四班", "第五班"):
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
            elif "評分" in h and "國際" not in h and "+/-" not in h and "rating" not in h_idx:
                h_idx["rating"] = i
            elif "配備" in h and "gear" not in h_idx:
                h_idx["gear"] = i

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
                    "weight": 120.0,
                    "jockey": "",
                    "trainer": "",
                    "rating": 40,
                    "gear": "-"
                }

            if "horse_name" in h_idx and h_idx["horse_name"] < len(cols):
                val = cols[h_idx["horse_name"]]
                cm = re.search(r"\((\w+)\)", val)
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

            if "rating" in h_idx and h_idx["rating"] < len(cols):
                val = cols[h_idx["rating"]].strip()
                if val.isdigit(): runners_map[h_no]["rating"] = int(val)

            if "gear" in h_idx and h_idx["gear"] < len(cols):
                val = cols[h_idx["gear"]].strip()
                if val and val not in ("--", "-"): runners_map[h_no]["gear"] = val

    horses = []
    for h_no, h in sorted(runners_map.items()):
        h["gear_info"] = parse_gear_features(h["gear"])
        horses.append(h)

    meta = {"venue": venue, "distance": distance, "track_type": track, "course": course, "race_class": race_class}
    return meta, horses

def run_upcoming():
    target_date, date_hkjc, venue = detect_upcoming_meeting()
    print(f"=== 香港賽馬 AI 智能預測系統 (即將開跑賽事: {target_date} {venue}) ===")
    print(f"🌟 賠率直連: https://bet.hkjc.com/ch/racing/wp/{target_date}/{venue}/\n")

    total_races = 0
    for race_no in range(1, 12):
        meta, horses = fetch_race_horses(date_hkjc, venue, race_no)
        if not meta or not horses:
            if race_no == 1:
                print(f"未能抓取到 {target_date} 第 1 場資料，請確認馬會官方是否已公佈完整排位。")
            break

        total_races += 1
        race_id = f"{target_date.replace('-', '')}_{venue}_{race_no:02d}"

        # 1. 抓取即時賠率 (直連 https://bet.hkjc.com/ch/racing/wp/)
        odds_map = fetch_live_odds(target_date, venue, race_no)

        # 2. 登記賽事基本資料
        supabase.table("races").upsert({
            "race_id": race_id, "race_date": target_date,
            "venue": meta["venue"], "race_no": race_no, "distance": meta["distance"],
            "track_type": meta["track_type"], "course": meta["course"],
            "race_class": meta["race_class"]
        }).execute()

        # 3. 核心實力評分
        ratings = [float(h["rating"]) for h in horses]
        avg_r = sum(ratings) / len(ratings) if ratings else 40.0
        weights = [float(h["weight"]) for h in horses]
        avg_w = sum(weights) / len(weights) if weights else 122.0

        scores = []
        for h in horses:
            r_score = (float(h["rating"]) - avg_r) / 10.0
            w_score = (avg_w - float(h["weight"])) / 10.0

            draw = h["draw"]
            if draw <= 3: d_score = 0.50
            elif draw <= 7: d_score = 0.20
            elif draw <= 10: d_score = -0.10
            else: d_score = -0.40

            j_score = ELITE_JOCKEYS.get(h["jockey"], 0.40)
            t_score = ELITE_TRAINERS.get(h["trainer"], 0.45)
            jt_score = (j_score * 0.70 + t_score * 0.30)

            g_score = h["gear_info"]["bonus"]

            total = (r_score * 0.35) + (w_score * 0.15) + (d_score * 0.15) + (jt_score * 0.30) + (g_score * 0.05)
            scores.append(total)

        # 4. 貝氏實戰融合勝率
        scores = np.array(scores)
        exp_s = np.exp(scores * 2.0)
        raw_probs = (exp_s / exp_s.sum()) * 100.0

        has_odds = any(h["horse_no"] in odds_map and odds_map[h["horse_no"]] > 1.0 for h in horses)
        if has_odds:
            market_implied = []
            for h in horses:
                o = odds_map.get(h["horse_no"], 0.0)
                market_implied.append(1.0 / o if o > 1.0 else 0.05)
            sum_mkt = sum(market_implied)
            mkt_probs = np.array([(m / sum_mkt) * 100.0 for m in market_implied])
            final_probs = 0.55 * mkt_probs + 0.45 * raw_probs
        else:
            mkt_probs = np.zeros(len(horses))
            final_probs = raw_probs

        scored = []
        for i, h in enumerate(horses):
            h_no = h["horse_no"]
            odds = odds_map.get(h_no)
            edge = float(raw_probs[i] - mkt_probs[i]) if has_odds else 0.0

            tags = list(h["gear_info"]["tags"])
            if h["draw"] <= 3: tags.append("🎯 黃金內檔")
            elif h["draw"] >= 11: tags.append("⚠️ 外檔考驗")
            if h["jockey"] in ELITE_JOCKEYS: tags.append("🔥 頂級騎師")
            if h.get("trainer"): tags.append(f"🎪 {h['trainer']}")
            if h.get("weight"): tags.append(f"⚖️ {int(h['weight'])}磅")
            tags.append("⏳ 體力黃金期")

            is_val = False
            if has_odds and odds and 3.5 <= odds <= 15.0 and edge >= 3.0:
                tags.append("💎 賠率超值 (超額價值)")
                is_val = True

            j_pct = round(ELITE_JOCKEYS.get(h["jockey"], 0.40) * 100, 1)

            # 騎練雙全組合顯示 (例如: 艾兆禮 / 蘇偉賢)
            jockey_trainer_str = f"{h['jockey']} / {h['trainer']}" if h.get("trainer") else h["jockey"]

            scored.append({
                "race_id": race_id,
                "horse_no": h_no,
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": jockey_trainer_str,
                "win_probability": round(float(final_probs[i]), 2),
                "gear": h["gear"],
                "rating": h["rating"],
                "smart_tags": tags,
                "combo_synergy": j_pct,
                "market_odds": odds,
                "is_value_bet": is_val
            })

        # 按融合勝率排序
        scored.sort(key=lambda x: x["win_probability"], reverse=True)
        final_payload = []
        for rank, item in enumerate(scored, 1):
            odds = item["market_odds"] or 5.0
            if rank == 1:
                strat = "🎯 獨贏首選 / 核心馬膽" if odds <= 8.0 else "⚡ 首選伏兵 / 冷門馬膽"
            elif rank == 2:
                strat = "⚡ 次選主力 / 連贏配腳"
            elif rank <= 4:
                strat = "🛡️ 連贏配腳 / 四連環"
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
                "is_value_bet": item["is_value_bet"] or (rank <= 2),
                "gear": item["gear"],
                "rating": item["rating"],
                "smart_tags": item["smart_tags"],
                "combo_synergy": item["combo_synergy"],
                "bet_strategy": strat,
                "market_odds": item["market_odds"]
            })

        supabase.table("race_predictions").delete().eq("race_id", race_id).execute()
        supabase.table("race_predictions").insert(final_payload).execute()
        top_h = scored[0]
        odds_count = sum(1 for p in final_payload if p.get("market_odds") is not None)
        print(f"  ✓ 第 {race_no} 場完成 (出賽: {len(horses)} 匹, 賠率匹配: {odds_count} 匹, 首選: {top_h['horse_name']} {top_h['horse_no']}號 [{top_h['jockey']}] 預測勝率:{top_h['win_probability']}%)")

    print(f"\n🎉 成功！已完成 {target_date} 共 {total_races} 場賽事預測與賠率更新並寫入 Supabase！")

if __name__ == "__main__":
    run_upcoming()
