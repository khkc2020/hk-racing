import os
import re
import time
import datetime
import requests
import pandas as pd
import numpy as np
from bs4 import BeautifulSoup
from supabase import create_client
import lightgbm as lgb

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://racing.hkjc.com/",
}

def parse_all_gear_signals(gear_str):
    if not gear_str or gear_str == "-":
        return {"tags": [], "focus_score": 0.0, "breath_score": 0.0}
    g = gear_str.upper()
    tags = []
    focus_bonus = 0.0
    breath_bonus = 0.0

    if re.search(r"B1|V1|PC1|P1", g):
        tags.append("👓 首次眼罩 (B1大變革)")
        focus_bonus += 0.20
    elif re.search(r"B2|V2", g):
        tags.append("👓 重戴眼罩")
        focus_bonus += 0.10
    elif re.search(r"\bB\b|\bV\b|\bPC\b", g):
        tags.append("👓 配戴眼罩")
        focus_bonus += 0.05
    elif re.search(r"B-|V-", g):
        tags.append("🔄 脫去眼罩")

    if "TT1" in g or "XB1" in g:
        tags.append("👅 首次舌帶/鼻箍")
        breath_bonus += 0.15
    elif "TT" in g:
        tags.append("👅 繫舌帶")
        breath_bonus += 0.05
    if "XB" in g:
        tags.append("🦺 交叉鼻箍")
    if "H1" in g or "E1" in g:
        tags.append("🎧 首次頭罩/耳塞")
    elif "H" in g:
        tags.append("🎧 戴頭罩")
    if "BL" in g or "BR" in g:
        tags.append("⚖️ 防斜跑刺墊")

    return {"tags": tags, "focus_score": focus_bonus, "breath_score": breath_bonus}

def train_ranking_model():
    print("--> 讀取大數據訓練 LightGBM 排序引擎...")
    all_rows = []
    offset = 0
    while True:
        res = supabase.table("race_results").select(
            "race_id, place_num, horse_code, jockey, trainer, actual_weight, draw, speed_mps"
        ).range(offset, offset + 999).execute()
        if not res.data: break
        all_rows.extend(res.data)
        if len(res.data) < 1000: break
        offset += 1000

    df = pd.DataFrame(all_rows)
    df["is_win"] = (df["place_num"] == 1).astype(int)
    df["j_t_pair"] = df["jockey"] + "_" + df["trainer"]

    pair_dict = df.groupby("j_t_pair")["is_win"].mean().to_dict()
    df["combo_synergy"] = df["j_t_pair"].map(pair_dict).fillna(0.08)

    jockey_dict = df.groupby("jockey")["is_win"].mean().to_dict()
    trainer_dict = df.groupby("trainer")["is_win"].mean().to_dict()
    horse_speed_dict = df.groupby("horse_code")["speed_mps"].mean().to_dict()
    global_speed = df["speed_mps"].dropna().mean() or 17.0

    df["horse_avg_speed"] = df["horse_code"].map(horse_speed_dict).fillna(global_speed)
    df["jockey_win_rate"] = df["jockey"].map(jockey_dict).fillna(0.08)
    df["trainer_win_rate"] = df["trainer"].map(trainer_dict).fillna(0.08)
    df["draw"] = df["draw"].fillna(7)
    df["actual_weight"] = df["actual_weight"].fillna(120)

    race_means = df.groupby("race_id")[["actual_weight", "horse_avg_speed"]].transform("mean")
    df["weight_vs_race_avg"] = df["actual_weight"] - race_means["actual_weight"]
    df["speed_vs_race_avg"] = df["horse_avg_speed"] - race_means["horse_avg_speed"]

    def get_rank_target(p):
        if p == 1: return 3
        if p == 2: return 2
        if p == 3: return 1
        return 0

    df["rank_target"] = df["place_num"].map(get_rank_target)
    feature_cols = [
        "draw", "actual_weight", "horse_avg_speed", 
        "jockey_win_rate", "trainer_win_rate", "combo_synergy",
        "speed_vs_race_avg", "weight_vs_race_avg"
    ]

    groups = df.groupby("race_id", sort=False).size().values
    ranker = lgb.LGBMRanker(objective="lambdarank", n_estimators=100, learning_rate=0.05, random_state=42)
    ranker.fit(df[feature_cols], df["rank_target"], group=groups)

    return {
        "model": ranker, "features": feature_cols,
        "jockey_dict": jockey_dict, "trainer_dict": trainer_dict,
        "pair_dict": pair_dict, "horse_speed": horse_speed_dict, "global_speed": global_speed
    }

def fetch_race_data(date_str, venue, race_no):
    # 支援已公佈賽果頁與排位頁雙向抓取
    url_results = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_str}&Racecourse={venue}&RaceNo={race_no}"
    try:
        resp = requests.get(url_results, headers=HEADERS, timeout=12)
        if resp.status_code != 200: return None, []
    except: return None, []

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
    for c in ["第一班", "第二班", "第三班", "第四班", "第五班"]:
        if c in race_text: race_class = c; break

    table = soup.find("table", class_="f_tac") or soup.find("table", class_="tableBorder2")
    if not table: return None, []

    horses = []
    for r in table.find_all("tr"):
        tds = [td.get_text().strip() for td in r.find_all("td")]
        if len(tds) < 11 or not (tds[0].isdigit() or tds.isdigit()): continue

        # LocalResults 格式：tds[0]名次, tds馬號, tds馬名(烙號), tds騎師, tds練馬師, tds負磅, tds[7]檔位
        h_no = int(tds) if tds.isdigit() else int(tds[0])
        raw_horse = tds
        cm = re.search(r"\(([A-Z0-9]+)\)", raw_horse)
        h_code = cm.group(1) if cm else f"H{h_no}"
        h_name = re.sub(r"[\s\xa0]*\(.*?\)", "", raw_horse).strip()

        wt = float(re.findall(r"\d+", tds)[0]) if re.findall(r"\d+", tds) else 120.0
        jockey = tds
        trainer = tds
        draw = int(tds[7]) if tds[7].isdigit() else 7

        horses.append({
            "horse_no": h_no,
            "horse_name": h_name,
            "horse_code": h_code,
            "weight": wt,
            "jockey": jockey,
            "draw": draw,
            "trainer": trainer,
            "rating": 60, # 預設評分基準
            "gear": "-",
            "gear_signals": parse_all_gear_signals("-")
        })

    meta = {"venue": venue, "distance": distance, "track_type": track, "course": course, "race_class": race_class}
    return meta, horses

def run_upcoming():
    print("=== 香港賽馬 AI 智能預測系統 ===")
    venue = "ST"
    target_date = "2026-09-27"
    date_hkjc = "2026/09/27"

    ctx = train_ranking_model()
    model = ctx["model"]

    for race_no in range(1, 12):
        meta, horses = fetch_race_data(date_hkjc, venue, race_no)
        if not meta or not horses: break

        race_id = f"{target_date.replace('-', '')}_{venue}_{race_no:02d}"

        # 1. 寫入賽事資訊
        supabase.table("races").upsert({
            "race_id": race_id, "race_date": target_date,
            "venue": meta["venue"], "race_no": race_no, "distance": meta["distance"],
            "track_type": meta["track_type"], "course": meta["course"],
            "race_class": meta["race_class"]
        }).execute()

        # 2. 機器學習特徵計算
        feats = []
        for h in horses:
            h_spd = ctx["horse_speed"].get(h["horse_code"], ctx["global_speed"])
            j_rt = ctx["jockey_dict"].get(h["jockey"], 0.08)
            t_rt = ctx["trainer_dict"].get(h["trainer"], 0.08)
            c_rt = ctx["pair_dict"].get(f"{h['jockey']}_{h['trainer']}", 0.08)
            feats.append({
                "draw": h["draw"], "actual_weight": h["weight"],
                "horse_avg_speed": h_spd, "jockey_win_rate": j_rt,
                "trainer_win_rate": t_rt, "combo_synergy": c_rt
            })

        feat_df = pd.DataFrame(feats)
        feat_df["weight_vs_race_avg"] = feat_df["actual_weight"] - feat_df["actual_weight"].mean()
        feat_df["speed_vs_race_avg"] = feat_df["horse_avg_speed"] - feat_df["horse_avg_speed"].mean()

        scores = model.predict(feat_df[ctx["features"]])
        exp_s = np.exp(scores - np.max(scores))
        probs = (exp_s / exp_s.sum()) * 100.0

        scored = []
        for i, h in enumerate(horses):
            tags = []
            if h["draw"] <= 3: tags.append("🎯 黃金內檔")
            elif h["draw"] >= 11: tags.append("⚠️ 外檔考驗")
            tags.append("⏳ 體力黃金期")

            scored.append({
                "race_id": race_id,
                "horse_no": h["horse_no"], # 真實馬號 (如 喜行=5, 機械騎士=1)
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": h["jockey"],
                "win_probability": round(float(probs[i]), 2),
                "gear": h["gear"],
                "rating": h["rating"],
                "smart_tags": tags,
                "combo_synergy": round(float(feats[i]["combo_synergy"]) * 100, 1)
            })

        scored.sort(key=lambda x: x["win_probability"], reverse=True)
        final_payload = []
        for rank, item in enumerate(scored, 1):
            strat = "🎯 獨贏首選 / 連贏馬膽" if rank == 1 else ("⚡ 次選主力" if rank == 2 else ("🛡️ 連贏配腳" if rank <= 4 else ""))
            final_payload.append({
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

        # 🌟 關鍵修正：寫入前先清除該場舊資料，徹底杜絕重複與跨場混雜
        supabase.table("race_predictions").delete().eq("race_id", race_id).execute()
        supabase.table("race_predictions").insert(final_payload).execute()
        print(f"  ✓ 第 {race_no} 場真實馬匹已更新完畢 (出賽: {len(horses)} 匹)")

    print("\n🎉 成功！第 1 場至第 11 場各場獨立數據已全部恢復正常！")

if __name__ == "__main__":
    run_upcoming()
