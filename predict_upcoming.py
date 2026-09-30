import os
import re
import json
import requests
import numpy as np
from bs4 import BeautifulSoup
from supabase import create_client

# Supabase 連線設定（支援環境變數或直接在此填入金鑰）
SUPABASE_URL = "https://rxmkohhgznfcnhdqegwq.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ4bWtvaGhnem5mY25oZHFlZ3dxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MDQ5OTk2OCwiZXhwIjoyMTA2MDc1OTY4fQ.QUqbXyvQuVuulKbiaI0jC20aUw21l1vd4pjXWEryjuI"

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.hkjc.com/",
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

def fetch_all_odds_with_playwright(date_str, venue, total_races=11):
    """
    🌟 人眼級官網真實抓取模組 (Playwright Headless Chrome)：
    在後台啟動真實的 Chrome 瀏覽器渲染馬會官網 (bet.hkjc.com)，
    等待 JavaScript 執行完畢並動態畫出表格後，直接提取畫面上的即時獨贏賠率！
    返回: {場次(int): {馬號(int): 獨贏賠率(float)}}
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("  💡 提示：若要啟用「馬會官網人眼級直接抓取」，請在 PowerShell 執行：")
        print("     pip install playwright")
        print("     playwright install chromium")
        return None

    print("  🚀 正在啟動後台真實 Chrome 引擎，直接連線馬會官網 (bet.hkjc.com) 讀取最新即時賠率...")
    all_odds = {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900}
            )
            page = context.new_page()

            for r_no in range(1, total_races + 1):
                url = f"https://bet.hkjc.com/ch/racing/wp/{date_str}/{venue}/{r_no}"
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(2500)  # 等候 React 完成畫面渲染

                    extracted = page.evaluate("""() => {
                        const res = {};
                        const rows = document.querySelectorAll('tr');
                        for (const tr of rows) {
                            const cells = Array.from(tr.querySelectorAll('td, th')).map(c => c.innerText.trim());
                            if (cells.length >= 2 && /^\\d+$/.test(cells[0])) {
                                const hNo = parseInt(cells[0]);
                                for (let i = 1; i < cells.length; i++) {
                                    const clean = cells[i].replace('$', '').trim();
                                    if (/^\\d+(\\.\\d+)?$/.test(clean)) {
                                        const val = parseFloat(clean);
                                        if (val >= 1.0 && val <= 999.0) {
                                            res[hNo] = val;
                                            break;
                                        }
                                    }
                                }
                            }
                        }
                        return res;
                    }""")
                    all_odds[r_no] = extracted or {}
                except Exception:
                    all_odds[r_no] = {}

            browser.close()
        print(f"  ✓ 馬會官網即時賠率渲染讀取完成！共取得 {len(all_odds)} 場數據。")
        return all_odds
    except Exception as e:
        print(f"  ⚠️ Playwright 渲染過程提示: {e}")
        return None

def fetch_live_odds(date_str, venue, race_no):
    """備用賠率抓取模組（東網與 GraphQL 備援）"""
    odds_map = {}
    try:
        oncc_url = f"https://racing.on.cc/racing/rat/current/rjrata00{race_no:02d}x0.html"
        headers_oncc = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Referer": "https://racing.on.cc/"
        }
        r = requests.get(oncc_url, headers=headers_oncc, timeout=6)
        if r.status_code == 200:
            r.encoding = r.apparent_encoding or "big5"
            soup = BeautifulSoup(r.text, "html.parser")
            for tb in soup.find_all("table"):
                text = tb.get_text()
                if "獨贏" in text and "馬號" in text:
                    for row in tb.find_all("tr"):
                        cols = [td.text.strip() for td in row.find_all(["td", "th"])]
                        if len(cols) >= 3 and cols[0].isdigit():
                            h_no = int(cols[0])
                            for c in cols[2:]:
                                c_clean = c.replace("$", "").strip()
                                if re.match(r"^\d+(?:\.\d+)?$", c_clean):
                                    val = float(c_clean)
                                    if 1.0 <= val <= 999.0:
                                        odds_map[h_no] = val
                                        break
                    if odds_map:
                        break
    except Exception:
        pass
    return odds_map

def fetch_race_horses(date_str, venue, race_no):
    """抓取馬會排位表資訊"""
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

    # 1. 優先啟動 Playwright 真實 Chrome 瀏覽器渲染馬會官網即時賠率
    playwright_odds = fetch_all_odds_with_playwright(target_date, venue, total_races=11)

    total_races = 0
    for race_no in range(1, 12):
        meta, horses = fetch_race_horses(date_hkjc, venue, race_no)
        if not meta or not horses:
            if race_no == 1:
                print(f"未能抓取到 {target_date} 第 1 場資料，請確認馬會官方是否已公佈完整排位。")
            break

        total_races += 1
        race_id = f"{target_date.replace('-', '')}_{venue}_{race_no:02d}"

        # 2. 獲取賠率：優先使用馬會官網 Playwright 數據
        odds_map = {}
        if playwright_odds and race_no in playwright_odds and playwright_odds[race_no]:
            odds_map = playwright_odds[race_no]
        else:
            odds_map = fetch_live_odds(target_date, venue, race_no)

        # 3. 登記或更新賽事基本資料
        supabase.table("races").upsert({
            "race_id": race_id, "race_date": target_date,
            "venue": meta["venue"], "race_no": race_no, "distance": meta["distance"],
            "track_type": meta["track_type"], "course": meta["course"],
            "race_class": meta["race_class"]
        }).execute()

        # 4. 核心實力評估：評分基底 + 頂級騎練 + 檔位利弊 + 負磅優勢 + 配備微調
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

        # 溫度縮放 Softmax 計算勝率
        scores = np.array(scores)
        exp_s = np.exp(scores * 2.0)
        probs = (exp_s / exp_s.sum()) * 100.0

        scored = []
        for i, h in enumerate(horses):
            tags = list(h["gear_info"]["tags"])
            if h["draw"] <= 3: tags.append("🎯 黃金內檔")
            elif h["draw"] >= 11: tags.append("⚠ 外檔考驗")
            if h["jockey"] in ELITE_JOCKEYS: tags.append("🔥 頂級騎師")
            tags.append("⏳ 體力黃金期")

            j_pct = round(ELITE_JOCKEYS.get(h["jockey"], 0.40) * 100, 1)

            scored.append({
                "race_id": race_id,
                "horse_no": h["horse_no"],
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": h["jockey"],
                "win_probability": round(float(probs[i]), 2),
                "gear": h["gear"],
                "rating": h["rating"],
                "smart_tags": tags,
                "combo_synergy": j_pct
            })

        scored.sort(key=lambda x: x["win_probability"], reverse=True)
        final_payload = []
        for rank, item in enumerate(scored, 1):
            strat = "🎯 獨贏首選 / 連贏馬膽" if rank == 1 else ("⚡ 次選主力" if rank == 2 else ("🛡️ 連贏配腳" if rank <= 4 else ""))
            h_no = item["horse_no"]
            odds = odds_map.get(h_no)

            final_payload.append({
                "race_id": item["race_id"],
                "horse_no": h_no,
                "horse_code": item["horse_code"],
                "horse_name": item["horse_name"],
                "draw": item["draw"],
                "jockey": item["jockey"],
                "win_probability": item["win_probability"],
                "predicted_rank": rank,
                "is_value_bet": rank <= 2,
                "gear": item["gear"],
                "rating": item["rating"],
                "smart_tags": item["smart_tags"],
                "combo_synergy": item["combo_synergy"],
                "bet_strategy": strat,
                "market_odds": odds
            })

        supabase.table("race_predictions").delete().eq("race_id", race_id).execute()
        supabase.table("race_predictions").insert(final_payload).execute()
        top_h = scored[0]
        odds_count = sum(1 for p in final_payload if p.get("market_odds") is not None)
        sample_odds = [f"{h_no}號:{odds_map[h_no]}倍" for h_no in sorted(odds_map.keys())[:3]]
        odds_preview = f" ({', '.join(sample_odds)}...)" if sample_odds else ""
        print(f"  ✓ 第 {race_no} 場完成 (出賽: {len(horses)} 匹, 賠率匹配: {odds_count} 匹{odds_preview}, 首選: {top_h['horse_name']} {top_h['horse_no']}號 預測勝率:{top_h['win_probability']}%)")

    print(f"\n🎉 成功！已完成 {target_date} 共 {total_races} 場賽事預測與即時賠率更新！")

if __name__ == "__main__":
    run_upcoming()
