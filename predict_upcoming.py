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

# 1. Supabase 連線憑證
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

def train_base_model():
    """從歷史數據庫訓練 AI 模型並提取騎練及馬匹歷史能力指標"""
    print("--> 正在讀取歷史數據以訓練預測引擎...")
    res = supabase.table("race_results").select(
        "place_num, horse_code, jockey, trainer, actual_weight, draw, speed_mps"
    ).execute()
    
    df = pd.DataFrame(res.data)
    if df.empty or len(df) < 50:
        raise ValueError("歷史賽果筆數不足，無法支撐模型預測。")
        
    df["is_win"] = (df["place_num"] == 1).astype(int)
    
    # 騎師勝率字典
    jockey_stats = df.groupby("jockey")["is_win"].mean().to_dict()
    # 練馬師勝率字典
    trainer_stats = df.groupby("trainer")["is_win"].mean().to_dict()
    # 馬匹歷史平均速度字典
    horse_speed = df.groupby("horse_code")["speed_mps"].mean().to_dict()
    global_avg_speed = df["speed_mps"].dropna().mean() or 17.0

    # 訓練模型
    df["horse_avg_speed"] = df["horse_code"].map(horse_speed).fillna(global_avg_speed)
    df["jockey_win_rate"] = df["jockey"].map(jockey_stats).fillna(0.08)
    df["trainer_win_rate"] = df["trainer"].map(trainer_stats).fillna(0.08)
    df["draw"] = df["draw"].fillna(7)
    df["actual_weight"] = df["actual_weight"].fillna(120)

    feature_cols = ["draw", "actual_weight", "horse_avg_speed", "jockey_win_rate", "trainer_win_rate"]
    X = df[feature_cols]
    y = df["is_win"]

    model = GradientBoostingClassifier(n_estimators=100, learning_rate=0.08, max_depth=3, random_state=42)
    model.fit(X, y)
    print("✓ 基礎預測模型訓練完成！")

    stats = {
        "jockey": jockey_stats,
        "trainer": trainer_stats,
        "horse_speed": horse_speed,
        "global_speed": global_avg_speed,
        "model": model,
        "features": feature_cols
    }
    return stats

def parse_racecard(race_no):
    """解析單場排位表"""
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceNo={race_no}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None, None
    except Exception:
        return None, None

    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")

    # 檢查是否已公佈
    if "資料將於稍後公佈" in resp.text:
        return None, None

    # 解析賽事基本資訊
    info_div = soup.find("div", class_="race_tab")
    if not info_div:
        return None, None
    race_text = info_div.get_text()

    # 抓取賽事日期
    d_match = re.search(r"(\d{1,2})[月/](\d{1,2})[日/](\d{4})|(\d{4})[-/](\d{1,2})[-/](\d{1,2})", race_text)
    venue = "ST" if "沙田" in race_text else "HV"
    distance_match = re.search(r"(\d{3,4})米", race_text)
    distance = int(distance_match.group(1)) if distance_match else 1200
    track_type = "全天候" if "全天候" in race_text else "草地"
    course_match = re.search(r'\"([A-C\+3]+)\"\s*賽道', race_text)
    course = course_match.group(1) if course_match else None

    # 尋找排位表格
    table = soup.find("table", class_="tableBorder2") or soup.find("table", class_="f_tac")
    if not table:
        return None, None

    rows = table.find_all("tr")
    horses = []

    for r in rows:
        tds = [td.get_text().strip() for td in r.find_all("td")]
        if len(tds) < 10:
            continue
            
        horse_num_str = tds[0]
        if not horse_num_str.isdigit():
            continue

        # 馬名通常包含烙號或在特定欄位
        horse_cell = tds[3] if len(tds) > 3 else ""
        code_match = re.search(r"\(([A-Z0-9]+)\)", horse_cell)
        horse_code = code_match.group(1) if code_match else ""
        horse_name = re.sub(r"[\s\xa0]*\([A-Z0-9]+\)", "", horse_cell).strip()

        # 負磅、騎師、檔位、練馬師
        wt_str = tds[4] if len(tds) > 4 else "120"
        jockey = tds[5] if len(tds) > 5 else ""
        draw_str = tds[6] if len(tds) > 6 else "7"
        trainer = tds[7] if len(tds) > 7 else ""

        if not horse_code or not jockey:
            continue

        horses.append({
            "horse_no": int(horse_num_str),
            "horse_name": horse_name,
            "horse_code": horse_code,
            "weight": float(re.findall(r"\d+", wt_str)[0]) if re.findall(r"\d+", wt_str) else 120.0,
            "jockey": jockey,
            "draw": int(draw_str) if draw_str.isdigit() else 7,
            "trainer": trainer
        })

    race_meta = {
        "venue": venue,
        "race_no": race_no,
        "distance": distance,
        "track_type": track_type,
        "course": course,
    }
    return race_meta, horses

def run_upcoming_prediction():
    print("=== 正在檢查下一期賽事排位表 ===")
    
    # 測試第 1 場是否已有排位
    meta, horses = parse_racecard(1)
    if not meta or not horses:
        print("💡 提示：下一期排位表尚未正式公佈（通常於賽前兩天中午 12:00 公佈）。系統保持待命！")
        return

    # 訓練模型與讀取歷史畫像
    stats = train_base_model()
    model = stats["model"]

    # 依序預測每場排位
    for r in range(1, 12):
        meta, horses = parse_racecard(r)
        if not meta or not horses:
            break

        print(f"\n正在預測 第 {r} 場 (出賽馬匹: {len(horses)} 匹)...")
        # 寫入 races 主表
        today_date = time.strftime("%Y-%m-%d")
        race_id = f"{today_date.replace('-', '')}_{meta['venue']}_{r:02d}"
        
        supabase.table("races").upsert({
            "race_id": race_id,
            "race_date": today_date,
            "venue": meta["venue"],
            "race_no": r,
            "distance": meta["distance"],
            "track_type": meta["track_type"],
            "course": meta["course"],
        }).execute()

        # 組裝特徵進行預測
        pred_rows = []
        for h in horses:
            h_speed = stats["horse_speed"].get(h["horse_code"], stats["global_speed"])
            j_rate = stats["jockey"].get(h["jockey"], 0.08)
            t_rate = stats["trainer"].get(h["trainer"], 0.08)

            feat = pd.DataFrame([{
                "draw": h["draw"],
                "actual_weight": h["weight"],
                "horse_avg_speed": h_speed,
                "jockey_win_rate": j_rate,
                "trainer_win_rate": t_rate
            }])
            raw_p = model.predict_proba(feat)[0]
            pred_rows.append({
                "race_id": race_id,
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": h["jockey"],
                "raw_p": raw_p
            })

        # 歸一化勝率與排名
        total_p = sum(x["raw_p"] for x in pred_rows)
        for x in pred_rows:
            x["win_probability"] = round((x["raw_p"] / total_p) * 100.0, 2) if total_p > 0 else 10.0

        pred_rows.sort(key=lambda x: x["win_probability"], reverse=True)
        final_payload = []
        for rank, item in enumerate(pred_rows, 1):
            final_payload.append({
                "race_id": item["race_id"],
                "horse_code": item["horse_code"],
                "horse_name": item["horse_name"],
                "draw": item["draw"],
                "jockey": item["jockey"],
                "win_probability": item["win_probability"],
                "predicted_rank": rank,
                "is_value_bet": rank <= 2  # 賽前無賠率時，頭二選標註為優選
            })

        supabase.table("race_predictions").upsert(final_payload, on_conflict="race_id,horse_code").execute()
        print(f"  ✓ 第 {r} 場預測完成並推送到手機端！")
        time.sleep(1)

    print("\n🎉 下一期賽事預測已全數更新至手機端！")

if __name__ == "__main__":
    run_upcoming_prediction()
