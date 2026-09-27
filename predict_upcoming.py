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

def fetch_table_horses(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=12)
        if resp.status_code != 200: return None, []
    except: return None, []

    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "html.parser")
    info_div = soup.find("div", class_="race_tab")
    race_text = info_div.get_text() if info_div else soup.text

    venue = "ST" if "沙田" in race_text else "HV"
    dist_m = re.search(r"(\d{3,4})米", race_text)
    distance = int(dist_m.group(1)) if dist_m else 1200
    track = "全天候" if "全天候" in race_text else "草地"
    course_m = re.search(r'\"([A-C\+3]+)\"\s*賽道', race_text)
    course = course_m.group(1) if course_m else None

    race_class = "第四班"
    for c in ["第一班", "第二班", "第三班", "第四班", "第五班"]:
        if c in race_text: race_class = c; break

    table = soup.find("table", class_="tableBorder2") or soup.find("table", class_="f_tac")
    if not table: return None, []

    # 1. 穿透定位表頭行
    header_tr = None
    for r in table.find_all("tr"):
        txt = r.get_text()
        if "馬名" in txt or "馬號" in txt:
            header_tr = r
            break
    if not header_tr: return None, []

    headers = [th.get_text().strip() for th in header_tr.find_all(["th", "td"])]
    h_idx = {}
    for i, h in enumerate(headers):
        hl = h.lower().strip()
        if ("馬號" in h or "馬匹編號" in h or hl == "no.") and "horse_no" not in h_idx: h_idx["horse_no"] = i
        elif "馬名" in h and "綵衣" not in h and "horse_name" not in h_idx: h_idx["horse_name"] = i
        elif "負磅" in h and "+/-" not in h and "weight" not in h_idx: h_idx["weight"] = i
        elif "騎師" in h and "jockey" not in h_idx: h_idx["jockey"] = i
        elif "檔位" in h and "draw" not in h_idx: h_idx["draw"] = i
        elif "練馬師" in h and "trainer" not in h_idx: h_idx["trainer"] = i
        elif "評分" in h and "國際" not in h and "+/-" not in h and "rating" not in h_idx: h_idx["rating"] = i
        elif "配備" in h and "gear" not in h_idx: h_idx["gear"] = i

    horses = []
    passed = False
    for r in table.find_all("tr"):
        if r == header_tr:
            passed = True
            continue
        if not passed: continue

        tds = [td.get_text().strip() for td in r.find_all(["td", "th"])]
        if not tds or "horse_no" not in h_idx or len(tds) <= h_idx["horse_no"]: continue
        if not tds[h_idx["horse_no"]].isdigit(): continue

        h_no = int(tds[h_idx["horse_no"]])
        raw_horse = tds[h_idx["horse_name"]] if "horse_name" in h_idx else ""
        cm = re.search(r"\(([A-Z0-9]+)\)", raw_horse)
        h_code = cm.group(1) if cm else f"H{h_no}"
        h_name = re.sub(r"[\s\xa0]*\(.*?\)", "", raw_horse).strip()

        wt = float(re.findall(r"\d+", tds[h_idx["weight"]])[0]) if "weight" in h_idx and re.findall(r"\d+", tds[h_idx["weight"]]) else 120.0
        jockey = tds[h_idx["jockey"]] if "jockey" in h_idx else ""
        draw = int(tds[h_idx["draw"]]) if "draw" in h_idx and tds[h_idx["draw"]].isdigit() else 7
        trainer = tds[h_idx["trainer"]] if "trainer" in h_idx else ""

        rating = None
        if "rating" in h_idx and re.findall(r"\d+", tds[h_idx["rating"]]):
            rating = int(re.findall(r"\d+", tds[h_idx["rating"]])[0])

        gear_str = tds[h_idx["gear"]] if "gear" in h_idx and len(tds) > h_idx["gear"] else "-"
        if not gear_str or gear_str in ("--", "-"): gear_str = "-"

        horses.append({
            "horse_no": h_no,
            "horse_name": h_name,
            "horse_code": h_code,
            "weight": wt,
            "jockey": jockey,
            "draw": draw,
            "trainer": trainer,
            "rating": rating,
            "gear": gear_str,
            "gear_signals": parse_all_gear_signals(gear_str)
        })

    meta = {"venue": venue, "distance": distance, "track_type": track, "course": course, "race_class": race_class}
    return meta, horses

def run_upcoming():
    print("=== 香港賽馬 AI 智能預測與修復引擎啟動 ===")
    
    # 先測試即將出賽排位表
    test_url = "https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceNo=1"
    r = requests.get(test_url, headers=HEADERS, timeout=10)
    
    is_upcoming_ready = (r.status_code == 200 and "資料將於稍後公佈" not in r.text and "馬名" in r.text)
    
    if is_upcoming_ready:
        print("✓ 偵測到下一期官方排位表已公佈！正在進行實時預測...")
        target_date = time.strftime("%Y-%m-%d")
        url_template = "https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceNo={}"
    else:
        print("💡 提示：下一期排位表尚未出爐（今日中午 12:00 公佈）。")
        print("🔄 正在自動補齊並修復最近賽事 (2026-09-27) 的真實馬號、評分與配備...")
        target_date = "2026-09-27"
        url_template = "https://racing.hkjc.com/racing/information/Chinese/racing/RaceCard.aspx?RaceDate=2026/09/27&Racecourse=ST&RaceNo={}"

    ctx = train_ranking_model()
    model = ctx["model"]

    for race_no in range(1, 12):
        u = url_template.format(race_no)
        meta, horses = fetch_table_horses(u)
        if not meta or not horses: break

        race_id = f"{target_date.replace('-', '')}_{meta['venue']}_{race_no:02d}"

        supabase.table("races").upsert({
            "race_id": race_id, "race_date": target_date,
            "venue": meta["venue"], "race_no": race_no, "distance": meta["distance"],
            "track_type": meta["track_type"], "course": meta["course"],
            "race_class": meta["race_class"]
        }).execute()

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
        for i, h in enumerate(horses):
            sig = h["gear_signals"]
            scores[i] += (sig["focus_score"] + sig["breath_score"])

        exp_s = np.exp(scores - np.max(scores))
        probs = (exp_s / exp_s.sum()) * 100.0

        scored = []
        for i, h in enumerate(horses):
            tags = list(h["gear_signals"]["tags"])
            if h["draw"] <= 3: tags.append("🎯 黃金內檔")
            elif h["draw"] >= 11: tags.append("⚠️ 外檔考驗")

            scored.append({
                "race_id": race_id,
                "horse_no": h["horse_no"], # 寫入真實馬號
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

        supabase.table("race_predictions").upsert(final_payload, on_conflict="race_id,horse_code").execute()
        print(f"  ✓ 第 {race_no} 場真實馬號/評分/配備寫入完畢！")

    print("\n🎉 成功！所有真實馬號、評分與配備已全數注入資料庫！")

if __name__ == "__main__":
    run_upcoming()
