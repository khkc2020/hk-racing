import os
import re
import time
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

def fetch_history_meeting(date_str, venue, race_no):
    d_slash = date_str.replace("-", "/")
    card_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={d_slash}&Racecourse={venue}&RaceNo={race_no}"
    res_url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={d_slash}&Racecourse={venue}&RaceNo={race_no}"

    # 1. 抓取排位表 (補齊 評分、配備、近績)
    card_runners = {}
    distance = 1200
    track = "草地"
    course = "A"
    race_class = "第四班"

    try:
        r_card = requests.get(card_url, headers=HEADERS, timeout=10)
        if r_card.status_code == 200 and ("馬名" in r_card.text or "騎師" in r_card.text):
            r_card.encoding = "utf-8"
            soup_c = BeautifulSoup(r_card.text, "html.parser")
            txt = soup_c.text
            
            dist_m = re.search(r"(\d{3,4})米", txt)
            if dist_m: distance = int(dist_m.group(1))
            if "全天候" in txt: track = "全天候"
            c_m = re.search(r'\"([A-C\+3]+)\"\s*賽道', txt)
            if c_m: course = c_m.group(1)
            for c in ("一級賽", "二級賽", "三級賽", "第一班", "第二班", "第三班", "第四班", "第五班"):
                if c in txt: race_class = c; break

            for tb in soup_c.find_all("table"):
                header_tr = tb.find("tr")
                if not header_tr: continue
                headers = [th.text.strip() for th in header_tr.find_all(["th", "td"])]
                h_idx = {}
                for i, h in enumerate(headers):
                    if ("馬匹編號" in h or "馬號" in h) and "no" not in h_idx: h_idx["no"] = i
                    elif "馬名" in h and "綵衣" not in h and "name" not in h_idx: h_idx["name"] = i
                    elif "烙號" in h and "code" not in h_idx: h_idx["code"] = i
                    elif "負磅" in h and "+/-" not in h and "weight" not in h_idx: h_idx["weight"] = i
                    elif "騎師" in h and "jockey" not in h_idx: h_idx["jockey"] = i
                    elif "檔位" in h and "draw" not in h_idx: h_idx["draw"] = i
                    elif "練馬師" in h and "trainer" not in h_idx: h_idx["trainer"] = i
                    elif "評分" in h and "+/-" not in h:
                        if "國際" in h: h_idx["intl_rat"] = i
                        elif "rat" not in h_idx: h_idx["rat"] = i
                    elif "近績" in h and "form" not in h_idx: h_idx["form"] = i
                    elif "配備" in h and "gear" not in h_idx: h_idx["gear"] = i

                if "no" not in h_idx: continue

                for r in tb.find_all("tr")[1:]:
                    cols = [td.text.strip() for td in r.find_all(["td", "th"])]
                    if len(cols) <= h_idx["no"] or not cols[h_idx["no"]].isdigit(): continue
                    h_no = int(cols[h_idx["no"]])
                    
                    if h_no not in card_runners:
                        card_runners[h_no] = {
                            "horse_no": h_no, "horse_name": f"馬匹{h_no}", "horse_code": f"H{h_no}",
                            "draw": 7, "weight": 122.0, "jockey": "", "trainer": "",
                            "rating": 60, "form": "-", "gear": "-"
                        }
                    
                    if "name" in h_idx and h_idx["name"] < len(cols):
                        val = cols[h_idx["name"]]
                        cm = re.search(r"\(([A-Z0-9]+)\)", val)
                        if cm: card_runners[h_no]["horse_code"] = cm.group(1)
                        c_name = re.sub(r"[\s\xa0]*\(.*?\)", "", val).strip()
                        if c_name and c_name != "-" and not c_name.isdigit():
                            card_runners[h_no]["horse_name"] = c_name
                    if "code" in h_idx and h_idx["code"] < len(cols):
                        val = cols[h_idx["code"]].replace("(", "").replace(")", "").strip()
                        if val and len(val) >= 3 and not val.isdigit():
                            card_runners[h_no]["horse_code"] = val
                    if "draw" in h_idx and h_idx["draw"] < len(cols) and cols[h_idx["draw"]].isdigit():
                        card_runners[h_no]["draw"] = int(cols[h_idx["draw"]])
                    if "weight" in h_idx and h_idx["weight"] < len(cols):
                        w_m = re.findall(r"\d+", cols[h_idx["weight"]])
                        if w_m: card_runners[h_no]["weight"] = float(w_m[0])
                    if "jockey" in h_idx and h_idx["jockey"] < len(cols) and cols[h_idx["jockey"]] != "-":
                        card_runners[h_no]["jockey"] = cols[h_idx["jockey"]].strip()
                    if "trainer" in h_idx and h_idx["trainer"] < len(cols) and cols[h_idx["trainer"]] != "-":
                        card_runners[h_no]["trainer"] = cols[h_idx["trainer"]].strip()
                    
                    rat = None
                    if "rat" in h_idx and cols[h_idx["rat"]].isdigit(): rat = int(cols[h_idx["rat"]])
                    elif "intl_rat" in h_idx and cols[h_idx["intl_rat"]].isdigit(): rat = int(cols[h_idx["intl_rat"]])
                    if rat is not None: card_runners[h_no]["rating"] = rat
                    if "form" in h_idx and h_idx["form"] < len(cols):
                        card_runners[h_no]["form"] = cols[h_idx["form"]].strip()
                    if "gear" in h_idx and h_idx["gear"] < len(cols):
                        g_val = cols[h_idx["gear"]].strip()
                        if g_val and g_val not in ("--", "-"): card_runners[h_no]["gear"] = g_val
    except Exception as e:
        print(f"  [RaceCard Error] {e}")

    # 2. 抓取賽果表 (補齊 名次、完成時間、獨贏賠率、賽後事件評語)
    result_runners = {}
    incident_reports = {}

    try:
        r_res = requests.get(res_url, headers=HEADERS, timeout=10)
        if r_res.status_code == 200 and "名次" in r_res.text:
            r_res.encoding = "utf-8"
            soup_r = BeautifulSoup(r_res.text, "html.parser")

            tb = soup_r.find("table", class_="f_tac") or soup_r.find("table", class_="table_bd")
            if tb:
                for row in tb.find_all("tr")[1:]:
                    tds = [td.text.strip() for td in row.find_all("td")]
                    if len(tds) < 10: continue
                    raw_pos = tds[0]
                    finish_pos = int(raw_pos) if raw_pos.isdigit() else None
                    if not tds.isdigit(): continue
                    h_no = int(tds)
                    
                    h_raw = tds[2]
                    cm = re.search(r"\(([A-Z0-9]+)\)", h_raw)
                    h_code = cm.group(1) if cm else f"H{h_no}"
                    h_name = re.sub(r"\(.*?\)", "", h_raw).strip()

                    jockey = tds[3]
                    trainer = tds[4]
                    act_wt = float(tds[5]) if tds[5].replace(".", "", 1).isdigit() else 122.0
                    body_wt = int(tds[6]) if tds[6].isdigit() else None
                    draw = int(tds[7]) if tds[7].isdigit() else 7
                    finish_time = tds[10] if len(tds) > 10 else None
                    win_odds = float(tds[11]) if len(tds) > 11 and tds[11].replace(".", "", 1).isdigit() else None

                    result_runners[h_no] = {
                        "horse_no": h_no, "horse_name": h_name, "horse_code": h_code,
                        "jockey": jockey, "trainer": trainer, "actual_weight": act_wt,
                        "declared_weight": body_wt, "draw": draw, "finish_position": finish_pos,
                        "finish_time": finish_time, "win_odds": win_odds
                    }

            # 抓取賽後評語與競賽事件報告
            report_text = ""
            for tag in soup_r.find_all(["div", "table", "p"]):
                if "競賽事件報告" in tag.text or "沿途走勢評述" in tag.text:
                    report_text += " " + tag.text

            for h_no, r_info in result_runners.items():
                name = r_info["horse_name"]
                m = re.search(rf"[「『]{name}[」』]([^。！？]+[。！？])", report_text)
                if m:
                    incident_reports[h_no] = m.group(0).strip()
                elif f"（「{name}」）" in report_text:
                    m2 = re.search(rf"[^。！？\n]*（「{name}」）[^。！？\n]*[。！？]", report_text)
                    if m2: incident_reports[h_no] = m2.group(0).strip()
                else:
                    incident_reports[h_no] = "走勢正常，無特別意外報告"
    except Exception as e:
        print(f"  [LocalResults Error] {e}")

    all_nos = sorted(set(list(card_runners.keys()) + list(result_runners.keys())))
    if not all_nos: return None, []

    merged_horses = []
    for no in all_nos:
        c = card_runners.get(no, {})
        r = result_runners.get(no, {})
        h_name = c.get("horse_name") or r.get("horse_name") or f"馬匹{no}"
        h_code = c.get("horse_code") or r.get("horse_code") or f"H{no}"
        jockey = c.get("jockey") or r.get("jockey") or ""
        trainer = c.get("trainer") or r.get("trainer") or ""
        draw = c.get("draw") or r.get("draw") or 7
        wt = c.get("weight") or r.get("actual_weight") or 122.0
        rat = c.get("rating", 60)
        gear = c.get("gear", "-")
        form = c.get("form", "-")
        pos = r.get("finish_position")
        ftime = r.get("finish_time")
        odds = r.get("win_odds")
        decl_wt = r.get("declared_weight")
        incident = incident_reports.get(no, "無特別報告")

        merged_horses.append({
            "horse_no": no, "horse_name": h_name, "horse_code": h_code,
            "jockey": jockey, "trainer": trainer, "draw": draw,
            "weight": wt, "rating": rat, "gear": gear, "form": form,
            "finish_position": pos, "finish_time": ftime, "win_odds": odds,
            "declared_weight": decl_wt, "incident_report": incident,
            "gear_info": parse_gear_features(gear),
            "form_score": parse_form_score(form)
        })

    meta = {"venue": venue, "distance": distance, "track_type": track, "course": course, "race_class": race_class}
    return meta, merged_horses

def run_full_backfill():
    print("=== 香港賽馬 開季歷史大數據全量補全與回測修復 ===")
    season_meetings = [
        ("2026-09-06", "ST", 10),
        ("2026-09-09", "HV", 8),
        ("2026-09-13", "ST", 10),
        ("2026-09-16", "HV", 8),
        ("2026-09-23", "HV", 8),
        ("2026-09-27", "ST", 10)
    ]

    for date_str, venue, expected_races in season_meetings:
        print(f"\n📅 正在全量修復賽日: {date_str} ({venue})...")
        for race_no in range(1, expected_races + 1):
            meta, horses = fetch_history_meeting(date_str, venue, race_no)
            if not meta or not horses:
                continue

            race_id = f"{date_str.replace('-', '')}_{venue}_{race_no:02d}"

            # 1. 補齊 races 主表
            supabase.table("races").upsert({
                "race_id": race_id, "race_date": date_str,
                "venue": meta["venue"], "race_no": race_no, "distance": meta["distance"],
                "track_type": meta["track_type"], "course": meta["course"],
                "race_class": meta["race_class"]
            }).execute()

            # 2. 補齊 race_results 表（含 評分、配備、評語）
            res_payload = []
            for h in horses:
                res_payload.append({
                    "race_id": race_id,
                    "horse_no": h["horse_no"],
                    "horse_code": h["horse_code"],
                    "horse_name": h["horse_name"],
                    "jockey": h["jockey"],
                    "trainer": h["trainer"],
                    "draw": h["draw"],
                    "actual_weight": h["weight"],
                    "declared_weight": h["declared_weight"],
                    "place_num": h["finish_position"],
                    "finish_time_str": h["finish_time"],
                    "win_odds": h["win_odds"],
                    "rating": h["rating"],
                    "gear": h["gear"],
                    "incident_report": h["incident_report"]
                })
            supabase.table("race_results").upsert(res_payload, on_conflict="race_id,horse_code").execute()

            # 3. 為歷史賽事生成 AI 預測數據並寫入 race_predictions
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

                scores.append(handicap_efficiency + form_s + d_score + (jt_score * 0.25) + g_score)

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
            pred_payload = []
            for rank, item in enumerate(scored, 1):
                strat = "🎯 獨贏首選 / 連贏馬膽" if rank == 1 else ("⚡ 次選主力" if rank == 2 else ("🛡️ 連贏配腳" if rank <= 4 else ""))
                pred_payload.append({
                    "race_id": item["race_id"],
                    "horse_no": item["horse_no"],
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
                    "bet_strategy": strat
                })

            supabase.table("race_predictions").delete().eq("race_id", race_id).execute()
            supabase.table("race_predictions").insert(pred_payload).execute()
            print(f"  ✓ 第 {race_no} 場修復完成 (評分/配備/評語已補齊，AI預測已生成)")

    print("\n🎉 大功告成！開季所有歷史場次的評分、配備、評語及回測預測數據已全數修復就緒！")

if __name__ == "__main__":
    run_full_backfill()
