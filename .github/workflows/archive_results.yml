import os
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from supabase import create_client

# ==============================================================================
# 🗄️ 香港賽馬 AI：全自動賽後賽果歸檔引擎 (每次完場自動累積 races 與 race_results)
# ==============================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rxmkohhgznfcnhdqegwq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

if not SUPABASE_KEY:
    raise RuntimeError("缺少環境變數 SUPABASE_KEY，請確認 GitHub Secrets 已正確設定。")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.hkjc.com/",
}


def parse_finish_time_to_sec(t_str, dist=1200):
    if not t_str:
        return dist * 0.058
    s = str(t_str).strip().replace(":", ".")
    parts = [p for p in s.split(".") if p.isdigit()]
    try:
        if len(parts) == 3:
            return int(parts[0]) * 60.0 + int(parts[1]) + int(parts[2]) / 100.0
        elif len(parts) == 2:
            return int(parts[0]) + int(parts[1]) / 100.0
    except Exception:
        pass
    return dist * 0.058


def detect_venue_from_results(date_slash):
    """自動探測指定賽日是沙田 (ST) 還是跑馬地 (HV)，若非賽日返回 None"""
    for v in ("ST", "HV"):
        url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_slash}&Racecourse={v}&RaceNo=1"
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200 and "名次" in r.text and "沒有相關資料" not in r.text:
                return v
        except Exception:
            continue
    return None


def archive_meeting(date_hyphen):
    date_slash = date_hyphen.replace("-", "/")
    venue = detect_venue_from_results(date_slash)
    if not venue:
        return 0

    print(f"📅 發現完賽賽日：{date_hyphen} ({venue})，開始歸檔...")
    success_races = 0

    for race_no in range(1, 13):
        res_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_slash}&Racecourse={venue}&RaceNo={race_no}"
        card_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={date_slash}&Racecourse={venue}&RaceNo={race_no}"

        try:
            resp_res = requests.get(res_url, headers=HEADERS, timeout=10)
            if resp_res.status_code != 200 or "名次" not in resp_res.text or "沒有相關資料" in resp_res.text:
                continue
            resp_res.encoding = "utf-8"
            soup_r = BeautifulSoup(resp_res.text, "html.parser")
            race_text = soup_r.get_text()

            dist_m = re.search(r"(\d{3,4})\s*米", race_text)
            distance = int(dist_m.group(1)) if dist_m else 1200
            track = "全天候跑道" if ("全天候" in race_text or "泥地" in race_text) else "草地"
            c_m = re.search(r'\"([A-C\+3]+)\"\s*賽道', race_text)
            course = c_m.group(1) if c_m else None

            class_m = re.search(r'(第[一二三四五]班|國際[一二三]級賽|[一二三]級賽|新馬賽|條件限制賽)', race_text)
            race_class = class_m.group(1) if class_m else "第四班"

            going_m = re.search(r'場地狀況\s*[:：]\s*([\u4e00-\u9fa5]+)', race_text)
            if not going_m:
                going_m = re.search(r'(好地至快地|好地至黏地|好地|快地|黏地|軟地|爛地|濕慢地|例常)', race_text)
            going = going_m.group(1) if going_m else "好地"

            race_id = f"{date_hyphen.replace('-', '')}_{venue}_{race_no:02d}"

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

            # 嘗試從排位表補全評分、配備與距今日數
            card_data = {}
            try:
                r_card = requests.get(card_url, headers=HEADERS, timeout=8)
                if r_card.status_code == 200 and "馬名" in r_card.text:
                    r_card.encoding = "utf-8"
                    soup_c = BeautifulSoup(r_card.text, "html.parser")
                    for tb in soup_c.find_all("table"):
                        header_tr = tb.find("tr")
                        if not header_tr:
                            continue
                        headers = [th.text.strip() for th in header_tr.find_all(["th", "td"])]
                        h_idx = {}
                        for i, h in enumerate(headers):
                            hc = re.sub(r"\s+", "", h)
                            if ("馬匹編號" in hc or "馬號" in hc) and "no" not in h_idx:
                                h_idx["no"] = i
                            elif "評分" in hc and "國際" not in hc and "+/-" not in hc and "rat" not in h_idx:
                                h_idx["rat"] = i
                            elif "配備" in hc and "gear" not in h_idx:
                                h_idx["gear"] = i
                            elif ("距今日數" in hc or "休息日" in hc) and "rest_days" not in h_idx:
                                h_idx["rest_days"] = i
                        if "no" not in h_idx:
                            continue
                        for row in tb.find_all("tr")[1:]:
                            cols = [td.text.strip() for td in row.find_all(["td", "th"])]
                            if len(cols) <= h_idx["no"] or not cols[h_idx["no"]].isdigit():
                                continue
                            h_no = int(cols[h_idx["no"]])
                            rat = None
                            if "rat" in h_idx and h_idx["rat"] < len(cols):
                                m_rt = re.search(r"\d+", cols[h_idx["rat"]])
                                if m_rt:
                                    rat = int(m_rt.group(0))
                            gear = cols[h_idx["gear"]] if "gear" in h_idx and h_idx["gear"] < len(cols) else "-"
                            if gear in ("--", ""):
                                gear = "-"
                            rd = None
                            if "rest_days" in h_idx and h_idx["rest_days"] < len(cols):
                                raw_rd = cols[h_idx["rest_days"]]
                                if raw_rd.isdigit():
                                    rd = int(raw_rd)
                                elif "初" in raw_rd:
                                    rd = 0
                            card_data[h_no] = {"rating": rat or 50, "gear": gear, "rest_days": rd}
            except Exception:
                pass

            # 解析賽果主表
            tb = None
            for cand in soup_r.find_all("table"):
                t_txt = cand.get_text()
                if "名次" in t_txt and "馬名" in t_txt and "檔位" in t_txt:
                    tb = cand
                    break
            if not tb:
                continue

            report_text = ""
            for tag in soup_r.find_all(["div", "table", "p"]):
                if "競賽事件報告" in tag.text or "沿途走勢評述" in tag.text:
                    report_text += " " + tag.text

            raw_entries = []
            for row in tb.find_all("tr")[1:]:
                tds = [td.get_text(" ", strip=True) for td in row.find_all("td")]
                if len(tds) < 10:
                    continue
                pos_str = tds[0].strip()
                place_num = int(pos_str) if pos_str.isdigit() else None
                if not tds[1].strip().isdigit():
                    continue
                h_no = int(tds[1].strip())

                raw_name = tds[2]
                cm = re.search(r"\(([A-Z0-9]+)\)", raw_name)
                h_code = cm.group(1) if cm else f"H{h_no}"
                h_name = re.sub(r"\(.*?\)", "", raw_name).strip()

                jockey = tds[3].strip()
                trainer = tds[4].strip()
                act_wt = float(tds[5]) if tds[5].replace(".", "", 1).isdigit() else 122.0
                decl_wt = int(tds[6]) if tds[6].isdigit() else None
                draw = int(tds[7]) if tds[7].isdigit() else 7
                running_pos = tds[9].strip() if len(tds) > 9 else ""
                finish_time = tds[10].strip() if len(tds) > 10 else None
                win_odds = float(tds[11]) if len(tds) > 11 and tds[11].replace(".", "", 1).isdigit() else None

                inc = "無特別意外報告"
                m_inc = re.search(rf"[「『]{re.escape(h_name)}[」』]([^。！？]+[。！？])", report_text)
                if m_inc:
                    inc = m_inc.group(0).strip()
                elif f"（「{h_name}」）" in report_text:
                    m2 = re.search(rf"[^。！？\n]*（「{re.escape(h_name)}」）[^。！？\n]*[。！？]", report_text)
                    if m2:
                        inc = m2.group(0).strip()

                c_info = card_data.get(h_no, {"rating": 50, "gear": "-", "rest_days": None})
                f_sec = parse_finish_time_to_sec(finish_time, distance)

                raw_entries.append({
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
                    "rest_days": c_info["rest_days"],
                    "running_position": running_pos,
                    "incident_report": inc,
                    "_f_sec": f_sec
                })

            if not raw_entries:
                continue

            # 推算末段 400 米秒數 (與回測引擎一致)
            winner_secs = [e["_f_sec"] for e in raw_entries if e["place_num"] == 1 and e["_f_sec"] > 40]
            winner_sec = min(winner_secs) if winner_secs else (distance * 0.0575)
            sections_count = max(2.5, distance / 400.0)
            avg_400m = winner_sec / sections_count
            pace_offset = 0.20 if distance <= 1000 else (0.35 if distance <= 1200 else (0.50 if distance <= 1400 else 0.65))
            base_final_400m = max(21.60, min(24.50, avg_400m - pace_offset))

            clean_entries = []
            for e in raw_entries:
                parts = [int(p) for p in str(e["running_position"]).split() if p.isdigit()]
                if len(parts) >= 2:
                    pos_gained = parts[-2] - parts[-1]
                else:
                    pos_gained = (6 - int(e["place_num"])) * 0.5 if e["place_num"] else 0.0
                time_gap = max(0.0, min(4.0, e["_f_sec"] - winner_sec))
                l400 = round(max(21.35, min(26.80, base_final_400m - (pos_gained * 0.13) + (time_gap * 0.22))), 2)

                row_db = {k: v for k, v in e.items() if not k.startswith("_")}
                row_db["last_400m_sec"] = l400
                if row_db["rest_days"] is None:
                    del row_db["rest_days"]
                clean_entries.append(row_db)

            try:
                supabase.table("race_results").upsert(clean_entries, on_conflict="race_id,horse_code").execute()
            except Exception as db_e:
                err_s = str(db_e)
                for opt_col in ("rest_days", "last_400m_sec"):
                    if opt_col in err_s:
                        clean_entries = [{k: v for k, v in row.items() if k != opt_col} for row in clean_entries]
                supabase.table("race_results").upsert(clean_entries, on_conflict="race_id,horse_code").execute()

            success_races += 1
            print(f"  ✓ 第 {race_no} 場賽果已歸檔 ({len(clean_entries)} 匹馬)")
            time.sleep(0.3)
        except Exception as e:
            print(f"  [提示 R{race_no}] {e}")
            continue

    return success_races


def run_recent_archive(lookback_days=14):
    hkt = timezone(timedelta(hours=8))
    today_hkt = datetime.now(hkt).date()
    print(f"=== 🗄️ 香港賽馬 AI：自動掃描近 {lookback_days} 日賽果歸檔 (基準日: {today_hkt}) ===")

    total_meetings = 0
    total_races = 0
    for delta in range(lookback_days, -1, -1):
        d = today_hkt - timedelta(days=delta)
        r_cnt = archive_meeting(d.strftime("%Y-%m-%d"))
        if r_cnt > 0:
            total_meetings += 1
            total_races += r_cnt

    print(f"\n🎉 歸檔完成！共更新 {total_meetings} 個賽馬日、{total_races} 場賽果至 Supabase race_results！")


if __name__ == "__main__":
    run_recent_archive(lookback_days=14)
