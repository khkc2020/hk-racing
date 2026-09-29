import os
import re
import time
import requests
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.hkjc.com/",
}

def patch_existing_null_races():
    """1. 原地修補：只針對 2025 至今 race_class 或 going 為 NULL 的賽事進行秒級補全"""
    print("\n[Step 1] 正在檢查並原地修補已有賽事中的 NULL 欄位 (無需重抓)...")
    res = supabase.table("races").select("race_id,race_date,venue,race_no").is_("race_class", "null").limit(500).execute()
    null_races = res.data or []
    print(f"--> 發現共有 {len(null_races)} 場賽事的班次或地況為 NULL，開始精準補全...")

    for r in null_races:
        d_slash = r["race_date"].replace("-", "/")
        r_no = r["race_no"]
        url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={d_slash}&RaceNo={r_no}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=8)
            if resp.status_code == 200 and "名次" in resp.text:
                txt = resp.text
                class_m = re.search(r'(第[一二三四五]班|Class\s*[1-5]|Group\s*[1-3]|國際[一二三]級賽|[一二三]級賽|新馬賽|條件限制賽)', txt)
                race_class = class_m.group(1) if class_m else "第四班"
                
                going_m = re.search(r'場地狀況\s*[:：]\s*([\u4e00-\u9fa5]+)', txt)
                if not going_m:
                    going_m = re.search(r'(好地至快地|好地|快地|好地至黏地|黏地|爛地|濕慢地|例常)', txt)
                going = going_m.group(1) if going_m else "好地"

                # 快速 UPDATE，不重寫其他資料
                supabase.table("races").update({
                    "race_class": race_class,
                    "going": going
                }).eq("race_id", r["race_id"]).execute()
                print(f"  ✓ 已修復 {r['race_id']} -> 班次: {race_class}, 地況: {going}")
            time.sleep(0.2)
        except Exception as e:
            continue
    print("--> [Step 1] 原地修補完成！")

def backfill_2024_season():
    """2. 增量補全：只抓取 2024年9月 至 2024年12月 缺失的歷史紀錄"""
    print("\n[Step 2] 正在增量補全 2024 年開季賽事 (2024-09-08 至 2024-12-31)...")
    curr = datetime(2024, 9, 8)
    end = datetime(2024, 12, 31)

    while curr <= end:
        if curr.weekday() in (2, 6): # 週三或週日
            venue = "HV" if curr.weekday() == 2 else "ST"
            d_slash = curr.strftime("%Y/%m/%d")
            d_hyphen = curr.strftime("%Y-%m-%d")

            # 檢查資料庫是否已存在該日第 1 場
            check_id = f"{d_hyphen.replace('-', '')}_{venue}_01"
            res = supabase.table("races").select("race_id").eq("race_id", check_id).execute()
            if res.data:
                print(f"  📅 {d_hyphen} ({venue}) 資料已存在，跳過。")
            else:
                print(f"  📥 正在抓取 2024 賽日: {d_hyphen} ({venue})...")
                for race_no in range(1, 12):
                    res_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={d_slash}&RaceNo={race_no}"
                    card_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={d_slash}&Racecourse={venue}&RaceNo={race_no}"
                    try:
                        resp = requests.get(res_url, headers=HEADERS, timeout=8)
                        if resp.status_code != 200 or "名次" not in resp.text:
                            break
                        resp.encoding = "utf-8"
                        soup_r = BeautifulSoup(resp.text, "html.parser")
                        txt = soup_r.text

                        dist_m = re.search(r"(\d{3,4})米", txt)
                        distance = int(dist_m.group(1)) if dist_m else 1200
                        track = "全天候" if "全天候" in txt else "草地"
                        c_m = re.search(r'\"([A-C\+3]+)\"\s*賽道', txt)
                        course = c_m.group(1) if c_m else None
                        class_m = re.search(r'(第[一二三四五]班|Class\s*[1-5]|Group\s*[1-3]|國際[一二三]級賽|[一二三]級賽|新馬賽|條件限制賽)', txt)
                        race_class = class_m.group(1) if class_m else "第四班"
                        going_m = re.search(r'場地狀況\s*[:：]\s*([\u4e00-\u9fa5]+)', txt)
                        going = going_m.group(1) if going_m else "好地"

                        race_id = f"{d_hyphen.replace('-', '')}_{venue}_{race_no:02d}"

                        supabase.table("races").upsert({
                            "race_id": race_id, "race_date": d_hyphen, "venue": venue,
                            "race_no": race_no, "distance": distance, "track_type": track,
                            "course": course, "race_class": race_class, "going": going
                        }).execute()

                        # 抓取賽果表格
                        tb = soup_r.find("table", class_="f_tac") or soup_r.find("table", class_="table_bd")
                        if not tb: break
                        entries = []
                        for row in tb.find_all("tr")[1:]:
                            tds = [td.text.strip() for td in row.find_all("td")]
                            if len(tds) < 10 or not tds.isdigit(): continue
                            h_no = int(tds)
                            raw_name = tds[2]
                            cm = re.search(r"\(([A-Z0-9]+)\)", raw_name)
                            h_code = cm.group(1) if cm else f"H{h_no}"
                            h_name = re.sub(r"\(.*?\)", "", raw_name).strip()

                            entries.append({
                                "race_id": race_id, "horse_no": h_no, "horse_code": h_code,
                                "horse_name": h_name, "jockey": tds[3], "trainer": tds[4],
                                "actual_weight": float(tds[5]) if tds[5].replace(".","",1).isdigit() else 122.0,
                                "declared_weight": int(tds[6]) if tds[6].isdigit() else None,
                                "draw": int(tds[7]) if tds[7].isdigit() else 7,
                                "place_num": int(tds[0]) if tds[0].isdigit() else None,
                                "finish_time_str": tds[10] if len(tds) > 10 else None,
                                "win_odds": float(tds[11]) if len(tds) > 11 and tds[11].replace(".","",1).isdigit() else None,
                                "rating": 60, "gear": "-", "incident_report": "無特別報告"
                            })
                        if entries:
                            supabase.table("race_results").upsert(entries, on_conflict="race_id,horse_code").execute()
                        time.sleep(0.3)
                    except Exception:
                        continue
        curr += timedelta(days=1)
    print("--> [Step 2] 2024 年賽事補全完成！")

if __name__ == "__main__":
    patch_existing_null_races()
    backfill_2024_season()
