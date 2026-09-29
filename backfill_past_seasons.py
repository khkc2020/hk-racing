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

def parse_meeting_races(date_slash, date_hyphen, venue):
    """
    抓取指定賽日的所有完賽場次，並寫入 races 與 race_results
    同時自動補全：班次 (race_class)、場地狀況 (going)、官方評分 (rating)、配備 (gear)、評語 (incident_report)
    """
    first_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_slash}&RaceNo=1"
    try:
        r = requests.get(first_url, headers=HEADERS, timeout=10)
        if r.status_code != 200 or "名次" not in r.text:
            return 0  # 該日非賽馬日
    except Exception:
        return 0

    success_races = 0
    # 常規賽事為 8 至 11 場
    for race_no in range(1, 12):
        res_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_slash}&RaceNo={race_no}"
        card_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={date_slash}&Racecourse={venue}&RaceNo={race_no}"

        try:
            resp_res = requests.get(res_url, headers=HEADERS, timeout=10)
            if resp_res.status_code != 200 or "名次" not in resp_res.text:
                break
            resp_res.encoding = "utf-8"
            soup_r = BeautifulSoup(resp_res.text, "html.parser")
            race_text = soup_r.text

            # 1. 賽事主資訊 (途程、班次、地況)
            dist_m = re.search(r"(\d{3,4})米", race_text)
            distance = int(dist_m.group(1)) if dist_m else 1200
            track = "全天候" if "全天候" in race_text else "草地"
            c_m = re.search(r'\"([A-C\+3]+)\"\s*賽道', race_text)
            course = c_m.group(1) if c_m else None
            
            class_m = re.search(r'(第[一二三四五]班|Class\s*[1-5]|Group\s*[1-3]|國際[一二三]級賽|[一二三]級賽|新馬賽|條件限制賽)', race_text)
            race_class = class_m.group(1) if class_m else "第四班"
            
            going_m = re.search(r'場地狀況\s*[:：]\s*([\u4e00-\u9fa5]+)', race_text)
            if not going_m:
                going_m = re.search(r'(好地至快地|好地|快地|好地至黏地|黏地|爛地|濕慢地|例常)', race_text)
            going = going_m.group(1) if going_m else "好地"

            race_id = f"{date_hyphen.replace('-', '')}_{venue}_{race_no:02d}"

            # 寫入 races 主表
            supabase.table("races").upsert({
                "race_id": race_id,
                "race_date": date_hyphen,
                "venue": venue,
                "race_no": race_no,
                "distance": distance,
                "track_type": track,
                "course": course,
                "race_class": race_class,
                "going": going
            }).execute()

            # 2. 抓取排位表補全 評分、配備、上賽距今日數
            card_data = {}
            try:
                r_card = requests.get(card_url, headers=HEADERS, timeout=8)
                if r_card.status_code == 200 and "馬名" in r_card.text:
                    r_card.encoding = "utf-8"
                    soup_c = BeautifulSoup(r_card.text, "html.parser")
                    for tb in soup_c.find_all("table"):
                        header_tr = tb.find("tr")
                        if not header_tr: continue
                        headers = [th.text.strip() for th in header_tr.find_all(["th", "td"])]
                        h_idx = {}
                        for i, h in enumerate(headers):
                            if ("馬匹編號" in h or "馬號" in h) and "no" not in h_idx: h_idx["no"] = i
                            elif "評分" in h and "+/-" not in h:
                                if "國際" in h: h_idx["intl_rat"] = i
                                elif "rat" not in h_idx: h_idx["rat"] = i
                            elif "配備" in h and "gear" not in h_idx: h_idx["gear"] = i
                            elif ("距今日數" in h or "休息日" in h) and "rest_days" not in h_idx: h_idx["rest_days"] = i

                        if "no" not in h_idx: continue

                        for row in tb.find_all("tr")[1:]:
                            cols = [td.text.strip() for td in row.find_all(["td", "th"])]
                            if len(cols) <= h_idx["no"] or not cols[h_idx["no"]].isdigit(): continue
                            h_no = int(cols[h_idx["no"]])
                            
                            rat = None
                            if "rat" in h_idx and cols[h_idx["rat"]].isdigit(): rat = int(cols[h_idx["rat"]])
                            elif "intl_rat" in h_idx and cols[h_idx["intl_rat"]].isdigit(): rat = int(cols[h_idx["intl_rat"]])

                            gear = cols[h_idx["gear"]] if "gear" in h_idx and h_idx["gear"] < len(cols) else "-"
                            if gear in ("--", ""): gear = "-"

                            rd = None
                            if "rest_days" in h_idx and h_idx["rest_days"] < len(cols):
                                raw_rd = cols[h_idx["rest_days"]]
                                if raw_rd.isdigit(): rd = int(raw_rd)
                                elif "初" in raw_rd: rd = 0

                            card_data[h_no] = {"rating": rat or 60, "gear": gear, "rest_days": rd}
            except Exception:
                pass

            # 3. 解析賽果表中的馬匹成績與評語
            tb = soup_r.find("table", class_="f_tac") or soup_r.find("table", class_="table_bd")
            if not tb: break

            report_text = ""
            for tag in soup_r.find_all(["div", "table", "p"]):
                if "競賽事件報告" in tag.text or "沿途走勢評述" in tag.text:
                    report_text += " " + tag.text

            entries = []
            for row in tb.find_all("tr")[1:]:
                tds = [td.text.strip() for td in row.find_all("td")]
                if len(tds) < 10: continue
                pos_str = tds[0]
                place_num = int(pos_str) if pos_str.isdigit() else None
                if not tds.isdigit(): continue
                h_no = int(tds)

                raw_name = tds[2]
                cm = re.search(r"\(([A-Z0-9]+)\)", raw_name)
                h_code = cm.group(1) if cm else f"H{h_no}"
                h_name = re.sub(r"\(.*?\)", "", raw_name).strip()

                jockey = tds[3]
                trainer = tds[4]
                act_wt = float(tds[5]) if tds[5].replace(".", "", 1).isdigit() else 122.0
                decl_wt = int(tds[6]) if tds[6].isdigit() else None
                draw = int(tds[7]) if tds[7].isdigit() else 7
                finish_time = tds[10] if len(tds) > 10 else None
                win_odds = float(tds[11]) if len(tds) > 11 and tds[11].replace(".", "", 1).isdigit() else None

                inc = "無特別意外報告"
                m_inc = re.search(rf"[「『]{h_name}[」』]([^。！？]+[。！？])", report_text)
                if m_inc: inc = m_inc.group(0).strip()
                elif f"（「{h_name}」）" in report_text:
                    m2 = re.search(rf"[^。！？\n]*（「{h_name}」）[^。！？\n]*[。！？]", report_text)
                    if m2: inc = m2.group(0).strip()

                c_info = card_data.get(h_no, {"rating": 60, "gear": "-", "rest_days": None})

                entries.append({
                    "race_id": race_id,
                    "horse_no": h_no,
                    "horse_code": h_code,
                    "horse_name": h_name,
                    "jockey": jockey,
                    "trainer": trainer,
                    "draw": draw,
                    "actual_weight": act_wt,
                    "declared_weight": decl_wt,
                    "place_num": place_num,
                    "finish_time_str": finish_time,
                    "win_odds": win_odds,
                    "rating": c_info["rating"],
                    "gear": c_info["gear"],
                    "incident_report": inc
                })

            if entries:
                supabase.table("race_results").upsert(entries, on_conflict="race_id,horse_code").execute()
                success_races += 1
            
            time.sleep(0.3)

        except Exception as e:
            print(f"  [Error R{race_no}] {e}")
            continue

    return success_races

def run_season_backfill():
    print("=== 香港賽馬 歷史賽季全量大數據自動歸檔 ===")
    # 自動遍歷 2024/25 及 2025/26 兩大馬季 (2024年9月 至 2026年7月)
    start_date = datetime(2024, 9, 1)
    end_date = datetime(2026, 7, 20)

    total_meetings = 0
    total_races = 0

    curr = start_date
    while curr <= end_date:
        # 跳過每年 7月中至8月底暑期休賽期
        if not (curr.month == 7 and curr.day > 18) and not (curr.month == 8):
            # 週三 (跑馬地夜賽) 或 週日 (沙田日賽)
            if curr.weekday() in (2, 6):
                venue = "HV" if curr.weekday() == 2 else "ST"
                d_slash = curr.strftime("%Y/%m/%d")
                d_hyphen = curr.strftime("%Y-%m-%d")
                
                print(f"--> 探測賽日: {d_hyphen} ({venue})...", end=" ", flush=True)
                r_count = parse_meeting_races(d_slash, d_hyphen, venue)
                if r_count > 0:
                    total_meetings += 1
                    total_races += r_count
                    print(f"✓ 成功抓取 {r_count} 場賽事入庫")
                else:
                    print("（非賽事日，跳過）")
                
                time.sleep(0.5)

        curr += timedelta(days=1)

    print(f"\n🎉 恭喜！歷史大數據歸檔完畢！共成功存入 {total_meetings} 個賽馬日、{total_races} 場真實賽果！")

if __name__ == "__main__":
    run_season_backfill()
