import os
import re
import time
import random
import requests
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
from supabase import create_client
from sklearn.ensemble import GradientBoostingClassifier

# 讀取環境變數中的 Supabase 憑證
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("找不到 SUPABASE_URL 或 SUPABASE_KEY 環境變數！")

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://racing.hkjc.com/",
}

def time_to_seconds(time_str: str):
    if not time_str or time_str.strip() in ["-", "---", ""]:
        return None
    time_str = time_str.strip().replace(":", ".")
    parts = time_str.split(".")
    try:
        p_first = 0
        p_second = 1
        p_third = 2
        if len(parts) == 3:
            return int(parts[p_first]) * 60 + int(parts[p_second]) + int(parts[p_third]) / 100.0
        elif len(parts) == 2:
            return int(parts[p_first]) + int(parts[p_second]) / 100.0
    except Exception:
        return None
    return None

def get_latest_meeting():
    """自動偵測馬會最新的賽事日期與場地"""
    url = "https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    
    # 預設為首頁顯示的最新日期
    links = soup.find_all("a", href=re.compile(r"RaceDate=\d{4}/\d{2}/\d{2}", re.I))
    for a in links:
        href = a["href"]
        d_match = re.search(r"RaceDate=(\d{4}/\d{2}/\d{2})", href, re.I)
        c_match = re.search(r"Racecourse=([A-Za-z]+)", href, re.I)
        if d_match:
            date_str = d_match.group(1)
            venue = c_match.group(1).upper() if c_match else "ST"
            return date_str, venue
            
    return None, None

def fetch_and_save_race(date_str, venue, race_no):
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_str}&Racecourse={venue}&RaceNo={race_no}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return False
    except Exception:
        return False
        
    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    table = soup.find("table", class_="f_tac")
    if not table:
        return False
        
    clean_date = date_str.replace("/", "")
    race_id = f"{clean_date}_{venue}_{race_no:02d}"
    
    race_info_text = ""
    info_div = soup.find("div", class_="race_tab")
    if info_div:
        race_info_text = info_div.get_text()
        
    distance_match = re.search(r"(\d{3,4})米", race_info_text)
    distance = int(distance_match.group(1)) if distance_match else None
    track_type = "全天候" if "全天候" in race_info_text else "草地"
    course_match = re.search(r'\"([A-C\+3]+)\"\s*賽道', race_info_text)
    course = course_match.group(1) if course_match else None
    
    race_payload = {
        "race_id": race_id,
        "race_date": date_str.replace('/', '-'),
        "venue": venue,
        "race_no": race_no,
        "distance": distance,
        "track_type": track_type,
        "course": course,
    }
    supabase.table("races").upsert(race_payload).execute()
    
    idx_place = 0
    idx_horse_no = 1
    idx_horse = 2
    idx_jockey = 3
    idx_trainer = 4
    idx_act_wt = 5
    idx_dec_wt = 6
    idx_draw = 7
    idx_pos = 9
    idx_time = 10
    idx_odds = 11

    tbody = table.find("tbody") or table
    rows = tbody.find_all("tr")
    
    entries = []
    for r in rows:
        tds = [td.get_text().strip() for td in r.find_all("td")]
        if len(tds) < 11:
            continue
            
        place_str = tds[idx_place]
        if not (place_str.isdigit() or place_str in ["WV", "PU", "DISQ", "UR", "FE"]):
            continue
            
        place_num = int(place_str) if place_str.isdigit() else None
        horse_num_str = tds[idx_horse_no]
        horse_no = int(horse_num_str) if horse_num_str.isdigit() else None
        
        raw_horse = tds[idx_horse]
        horse_code = ""
        horse_name = raw_horse
        code_match = re.search(r"\(([A-Z0-9]+)\)", raw_horse)
        if code_match:
            horse_code = code_match.group(1)
            horse_name = re.sub(r"[\s\xa0]*\([A-Z0-9]+\)", "", raw_horse).strip()
            
        if not horse_code:
            continue
            
        jockey = tds[idx_jockey]
        trainer = tds[idx_trainer]
        
        def safe_float(val):
            val_clean = val.replace(',', '').strip()
            return float(val_clean) if re.match(r'^-?\d+(\.\d+)?$', val_clean) else None
            
        def safe_int(val):
            val_clean = val.strip()
            return int(val_clean) if val_clean.isdigit() else None

        actual_wt = safe_float(tds[idx_act_wt])
        declared_wt = safe_float(tds[idx_dec_wt])
        draw = safe_int(tds[idx_draw])
        running_pos = " ".join(tds[idx_pos].split())
        finish_time_str = tds[idx_time]
        finish_sec = time_to_seconds(finish_time_str)
        
        speed_mps = None
        if distance and finish_sec and finish_sec > 0:
            speed_mps = round(distance / finish_sec, 2)
            
        win_odds = None
        if len(tds) > idx_odds:
            win_odds = safe_float(tds[idx_odds])
        
        entries.append({
            "race_id": race_id,
            "place": place_str,
            "place_num": place_num,
            "horse_no": horse_no,
            "horse_name": horse_name,
            "horse_code": horse_code,
            "jockey": jockey,
            "trainer": trainer,
            "actual_weight": actual_wt,
            "declared_weight": declared_wt,
            "draw": draw,
            "running_position": running_pos,
            "finish_time_str": finish_time_str,
            "finish_time_seconds": finish_sec,
            "speed_mps": speed_mps,
            "win_odds": win_odds,
        })
        
    if entries:
        supabase.table("race_results").upsert(entries, on_conflict="race_id,horse_code").execute()
        return len(entries)
    return 0

def run_prediction_pipeline():
    """讀取全量數據並更新 AI 預測"""
    print("正在執行 AI 預測模型訓練與預測...")
    results_res = supabase.table("race_results").select(
        "id, race_id, place_num, horse_no, horse_name, horse_code, jockey, trainer, actual_weight, declared_weight, draw, speed_mps, win_odds"
    ).execute()
    
    df = pd.DataFrame(results_res.data)
    if df.empty:
        return
        
    df["is_win"] = (df["place_num"] == 1).astype(int)
    
    jockey_stats = df.groupby("jockey")["is_win"].agg(["count", "mean"]).reset_index()
    jockey_stats.columns = ["jockey", "jockey_races", "jockey_win_rate"]
    df = df.merge(jockey_stats[["jockey", "jockey_win_rate"]], on="jockey", how="left")

    trainer_stats = df.groupby("trainer")["is_win"].agg(["count", "mean"]).reset_index()
    trainer_stats.columns = ["trainer", "trainer_races", "trainer_win_rate"]
    df = df.merge(trainer_stats[["trainer", "trainer_win_rate"]], on="trainer", how="left")

    horse_speed = df.groupby("horse_code")["speed_mps"].mean().reset_index()
    horse_speed.columns = ["horse_code", "horse_avg_speed"]
    df = df.merge(horse_speed, on="horse_code", how="left")

    global_avg_speed = df["speed_mps"].dropna().mean() if not df["speed_mps"].dropna().empty else 17.0
    df["horse_avg_speed"] = df["horse_avg_speed"].fillna(global_avg_speed)
    df["draw"] = df["draw"].fillna(7)
    df["actual_weight"] = df["actual_weight"].fillna(120)
    df["jockey_win_rate"] = df["jockey_win_rate"].fillna(0.08)
    df["trainer_win_rate"] = df["trainer_win_rate"].fillna(0.08)

    feature_cols = ["draw", "actual_weight", "horse_avg_speed", "jockey_win_rate", "trainer_win_rate"]
    X = df[feature_cols]
    y = df["is_win"]

    model = GradientBoostingClassifier(n_estimators=100, learning_rate=0.08, max_depth=3, random_state=42)
    model.fit(X, y)

    prob_matrix = model.predict_proba(X)
    col_win_idx = 1
    df["raw_prob"] = prob_matrix[:, col_win_idx]

    predictions_to_save = []
    for race_id, group in df.groupby("race_id"):
        total_p = group["raw_prob"].sum()
        group = group.copy()
        group["norm_prob"] = (group["raw_prob"] / total_p) * 100.0 if total_p > 0 else 100.0 / len(group)
        group = group.sort_values(by="norm_prob", ascending=False)
        group["predicted_rank"] = range(1, len(group) + 1)
        
        for _, row in group.iterrows():
            p_pct = round(float(row["norm_prob"]), 2)
            odds = float(row["win_odds"]) if pd.notnull(row["win_odds"]) and row["win_odds"] > 0 else None
            is_value = False
            if odds and odds > 1:
                market_implied = (1.0 / odds) * 100.0
                if p_pct > market_implied * 1.25 and row["predicted_rank"] <= 4:
                    is_value = True
                    
            predictions_to_save.append({
                "race_id": race_id,
                "horse_code": row["horse_code"],
                "horse_name": row["horse_name"],
                "draw": int(row["draw"]) if pd.notnull(row["draw"]) else None,
                "jockey": row["jockey"],
                "win_probability": p_pct,
                "predicted_rank": int(row["predicted_rank"]),
                "market_odds": odds,
                "is_value_bet": is_value
            })

    batch_size = 100
    for i in range(0, len(predictions_to_save), batch_size):
        batch = predictions_to_save[i:i+batch_size]
        supabase.table("race_predictions").upsert(batch, on_conflict="race_id,horse_code").execute()
    print("✓ AI 預測數據已成功更新至 Supabase！")

if __name__ == "__main__":
    latest_date, venue = get_latest_meeting()
    if latest_date:
        print(f"最新賽事日期: {latest_date} ({venue})，開始抓取最新賽果...")
        for r in range(1, 12):
            count = fetch_and_save_race(latest_date, venue, r)
            if count == 0:
                break
            print(f"  ✓ 第 {r} 場完成 ({count} 匹馬)")
            time.sleep(random.uniform(1.5, 2.5))
            
        run_prediction_pipeline()
        print("=== 全流程自動運行完成！ ===")
    else:
        print("未偵測到最新賽日。")
