import os
import re
import time
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

def train_ranking_model():
    """使用全庫大數據訓練 LightGBM 賽馬排序引擎"""
    print("--> 正在讀取大數據以訓練 LightGBM 排序引擎...")
    all_rows = []
    page_size = 1000
    offset = 0
    while True:
        res = supabase.table("race_results").select(
            "race_id, place_num, horse_code, jockey, trainer, actual_weight, draw, speed_mps"
        ).range(offset, offset + page_size - 1).execute()
        if not res.data: break
        all_rows.extend(res.data)
        if len(res.data) < page_size: break
        offset += page_size

    df = pd.DataFrame(all_rows)
    df["is_win"] = (df["place_num"] == 1).astype(int)
    df["j_t_pair"] = df["jockey"] + "_" + df["trainer"]

    pair_stats = df.groupby("j_t_pair")["is_win"].agg(["count", "mean"]).reset_index()
    pair_dict = pair_stats.set_index("j_t_pair")["mean"].to_dict()
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

    # 同場相對特徵
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
    ranker = lgb.LGBMRanker(
        objective="lambdarank",
        n_estimators=120,
        learning_rate=0.05,
        num_leaves=31,
        random_state=42
    )
    ranker.fit(df[feature_cols], df["rank_target"], group=groups)
    print("✓ 賽事排位排序引擎訓練就緒！")

    return {
        "model": ranker,
        "features": feature_cols,
        "jockey_dict": jockey_dict,
        "trainer_dict": trainer_dict,
        "pair_dict": pair_dict,
        "horse_speed": horse_speed_dict,
        "global_speed": global_speed
    }

def parse_racecard_with_gear(race_no):
    """解析排位表，深度提取配備代號 (B/B1/TT) 與評分"""
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceNo={race_no}"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code != 200 or "資料將於稍後公佈" in resp.text:
            return None, None
    except:
        return None, None

    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    info_div = soup.find("div", class_="race_tab")
    if not info_div: return None, None
    race_text = info_div.get_text()

    venue = "ST" if "沙田" in race_text else "HV"
    dist_m = re.search(r"(\d{3,4})米", race_text)
    distance = int(dist_m.group(1)) if dist_m else 1200
    track = "全天候" if "全天候" in race_text else "草地"
    course_m = re.search(r'\"([A-C\+3]+)\"\s*賽道', race_text)
    course = course_m.group(1) if course_m else None

    # 班次 (Class)
    race_class = "第四班"
    if "第一班" in race_text: race_class = "第一班"
    elif "第二班" in race_text: race_class = "第二班"
    elif "第三班" in race_text: race_class = "第三班"
    elif "第五班" in race_text: race_class = "第五班"

    table = soup.find("table", class_="tableBorder2") or soup.find("table", class_="f_tac")
    if not table: return None, None

    horses = []
    for r in table.find_all("tr"):
        tds = [td.get_text().strip() for td in r.find_all("td")]
        if len(tds) < 10 or not tds[0].isdigit():
            continue

        raw_horse = tds[3] if len(tds) > 3 else ""
        cm = re.search(r"\(([A-Z0-9]+)\)", raw_horse)
        if not cm: continue
        h_code = cm.group(1)
        h_name = re.sub(r"[\s\xa0]*\([A-Z0-9]+\)", "", raw_horse).strip()

        wt_str = tds[4] if len(tds) > 4 else "120"
        wt = float(re.findall(r"\d+", wt_str)[0]) if re.findall(r"\d+", wt_str) else 120.0
        jockey = tds[5] if len(tds) > 5 else ""
        draw_str = tds[6] if len(tds) > 6 else "7"
        trainer = tds[7] if len(tds) > 7 else ""
        rating_str = tds[8] if len(tds) > 8 else "50"
        rating = int(re.findall(r"\d+", rating_str)[0]) if re.findall(r"\d+", rating_str) else 50
        
        # 配備欄位 (通常位於後方第 12 格或文字末端)
        gear_str = tds[-1] if len(tds) >= 12 else ""
        is_b1 = bool(re.search(r"B1|V1|P1", gear_str, re.I))

        horses.append({
            "horse_no": int(tds[0]),
            "horse_name": h_name,
            "horse_code": h_code,
            "weight": wt,
            "jockey": jockey,
            "draw": int(draw_str) if draw_str.isdigit() else 7,
            "trainer": trainer,
            "rating": rating,
            "gear": gear_str,
            "is_b1": is_b1
        })

    race_meta = {
        "venue": venue, "race_no": race_no, "distance": distance,
        "track_type": track, "course": course, "race_class": race_class
    }
    return race_meta, horses

def run_upcoming():
    print("=== 正在檢測即將出賽排位表 (含配備與評分) ===")
    meta, test_h = parse_racecard_with_gear(1)
    if not meta or not test_h:
        print("💡 提示：下一期排位表尚未正式公佈（通常於賽前兩天中午 12:00 公佈），系統保持待命！")
        return

    ctx = train_ranking_model()
    model = ctx["model"]

    for r in range(1, 12):
        meta, horses = parse_racecard_with_gear(r)
        if not meta or not horses: break

        print(f"\n正在深度分析 第 {r} 場 (出賽馬匹: {len(horses)} 匹)...")
        today_date = time.strftime("%Y-%m-%d")
        race_id = f"{today_date.replace('-', '')}_{meta['venue']}_{r:02d}"

        supabase.table("races").upsert({
            "race_id": race_id, "race_date": today_date,
            "venue": meta["venue"], "race_no": r, "distance": meta["distance"],
            "track_type": meta["track_type"], "course": meta["course"],
            "race_class": meta["race_class"]
        }).execute()

        # 組裝特徵 DataFrame
        feats = []
        for h in horses:
            h_spd = ctx["horse_speed"].get(h["horse_code"], ctx["global_speed"])
            j_rt = ctx["jockey_dict"].get(h["jockey"], 0.08)
            t_rt = ctx["trainer_dict"].get(h["trainer"], 0.08)
            c_rt = ctx["pair_dict"].get(f"{h['jockey']}_{h['trainer']}", 0.08)

            feats.append({
                "draw": h["draw"],
                "actual_weight": h["weight"],
                "horse_avg_speed": h_spd,
                "jockey_win_rate": j_rt,
                "trainer_win_rate": t_rt,
                "combo_synergy": c_rt,
                "is_b1": h["is_b1"],
                "gear": h["gear"],
                "rating": h["rating"]
            })
        
        feat_df = pd.DataFrame(feats)
        # 同場相對優勢
        feat_df["weight_vs_race_avg"] = feat_df["actual_weight"] - feat_df["actual_weight"].mean()
        feat_df["speed_vs_race_avg"] = feat_df["horse_avg_speed"] - feat_df["horse_avg_speed"].mean()

        scores = model.predict(feat_df[ctx["features"]])
        # 初戴眼罩 (B1) 給予正向激勵偏置 (+0.15)
        for i, h in enumerate(horses):
            if h["is_b1"]: scores[i] += 0.15

        # Softmax 轉換為標準勝率
        exp_s = np.exp(scores - np.max(scores))
        probs = (exp_s / exp_s.sum()) * 100.0

        scored_horses = []
        for i, h in enumerate(horses):
            tags = []
            if h["draw"] <= 3: tags.append("🎯 黃金內檔")
            elif h["draw"] >= 11: tags.append("⚠️ 外檔考驗")
            if h["is_b1"]: tags.append("👓 初戴眼罩 (B1)")
            if "TT" in h["gear"].upper(): tags.append("👅 繫舌帶")

            scored_horses.append({
                "race_id": race_id,
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": h["jockey"],
                "win_probability": round(float(probs[i]), 2),
                "gear": h["gear"],
                "rating": h["rating"],
                "is_b1": h["is_b1"],
                "smart_tags": tags,
                "combo_synergy": round(float(feats[i]["combo_synergy"]) * 100, 1)
            })

        scored_horses.sort(key=lambda x: x["win_probability"], reverse=True)
        final_payload = []
        for rank, item in enumerate(scored_horses, 1):
            strat = ""
            if rank == 1: strat = "🎯 獨贏首選 / 連贏馬膽"
            elif rank == 2: strat = "⚡ 次選主力"
            elif rank <= 4: strat = "🛡️ 連贏配腳"

            if rank == 1: item["smart_tags"].insert(0, "👑 AI頭選")
            elif rank <= 3: item["smart_tags"].insert(0, "⚡ 主力爭位")

            final_payload.append({
                "race_id": item["race_id"],
                "horse_code": item["horse_code"],
                "horse_name": item["horse_name"],
                "draw": item["draw"],
                "jockey": item["jockey"],
                "win_probability": item["win_probability"],
                "predicted_rank": rank,
                "is_value_bet": rank <= 2,
                "gear": item["gear"],
                "rating": item["rating"],
                "is_b1": item["is_b1"],
                "smart_tags": item["smart_tags"],
                "combo_synergy": item["combo_synergy"],
                "bet_strategy": strat
            })

        supabase.table("race_predictions").upsert(final_payload, on_conflict="race_id,horse_code").execute()
        print(f"  ✓ 第 {r} 場分析完成！已識別配備代號並推送至手機！")
        time.sleep(0.5)

    print("\n🎉 下一期賽事【配備變動與排序預測】已全部送達手機！")

if __name__ == "__main__":
    run_upcoming()
