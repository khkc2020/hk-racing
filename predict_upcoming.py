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

def fetch_race_data(date_str, venue, race_no):
    # 優先從包含官方評分與配備的排位表 (RaceCard) 抓取
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

    target_table = None
    header_tr = None
    for tb in soup.find_all("table"):
        for r in tb.find_all("tr"):
            t = r.get_text()
            if "馬名" in t and ("評分" in t or "騎師" in t):
                target_table = tb
                header_tr = r
                break
        if target_table: break

    if not target_table: return None, []

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
    for r in target_table.find_all("tr"):
        if r == header_tr:
            passed = True
            continue
        if not passed: continue

        tds = [td.get_text().strip() for td in r.find_all(["td", "th"])]
        if not tds: continue

        h_no = None
        if "horse_no" in h_idx and len(tds) > h_idx["horse_no"] and tds[h_idx["horse_no"]].isdigit():
            h_no = int(tds[h_idx["horse_no"]])
        elif len(tds) > 1 and tds.isdigit():
            h_no = int(tds)
        elif len(tds) > 0 and tds[0].isdigit():
            h_no = int(tds[0])

        if h_no is None: continue

        raw_horse = tds[h_idx["horse_name"]] if "horse_name" in h_idx and len(tds) > h_idx["horse_name"] else (tds if len(tds) > 2 else "")
        cm = re.search(r"\(([A-Z0-9]+)\)", raw_horse)
        h_code = cm.group(1) if cm else f"H{h_no}"
        h_name = re.sub(r"[\s\xa0]*\(.*?\)", "", raw_horse).strip()

        wt = float(re.findall(r"\d+", tds[h_idx["weight"]])[0]) if "weight" in h_idx and len(tds) > h_idx["weight"] and re.findall(r"\d+", tds[h_idx["weight"]]) else 120.0
        jockey = tds[h_idx["jockey"]] if "jockey" in h_idx and len(tds) > h_idx["jockey"] else (tds if len(tds) > 3 else "")
        trainer = tds[h_idx["trainer"]] if "trainer" in h_idx and len(tds) > h_idx["trainer"] else (tds if len(tds) > 4 else "")
        draw = int(tds[h_idx["draw"]]) if "draw" in h_idx and len(tds) > h_idx["draw"] and tds[h_idx["draw"]].isdigit() else 7

        # 🌟 精準提取官方真實評分 (例如 30, 39, 36)
        rating = None
        if "rating" in h_idx and len(tds) > h_idx["rating"] and re.findall(r"\d+", tds[h_idx["rating"]]):
            rating = int(re.findall(r"\d+", tds[h_idx["rating"]])[0])

        # 🌟 精準提取官方配備 (例如 BT, V, B2T)
        gear = tds[h_idx["gear"]] if "gear" in h_idx and len(tds) > h_idx["gear"] else "-"
        if not gear or gear in ("--", "-"): gear = "-"

        gear_signals = parse_all_gear_signals(gear)

        horses.append({
            "horse_no": h_no,
            "horse_name": h_name,
            "horse_code": h_code,
            "weight": wt,
            "jockey": jockey,
            "draw": draw,
            "trainer": trainer,
            "rating": rating,
            "gear": gear,
            "gear_signals": gear_signals
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
        feat_df["weight_vs_race_avg"] = feat以下為精簡核心版 `predict_upcoming.py`，完整支援**真實馬號、真實評分與官方配備**，並修正場次獨立性：

```python
import os, re, time, requests, numpy as np, pandas as pd
from bs4 import BeautifulSoup
from supabase import create_client
import lightgbm as lgb

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
HEADERS = {"User-Agent": "Mozilla/5.0"}

def parse_gear(g):
    if not g or g == "-": return [], 0.0
    t, b, gu = [], 0.0, g.upper()
    if re.search(r"B1|V1|PC1|P1", gu): t.append("👓 初戴眼罩"); b += 0.2
    elif re.search(r"\bB\b|\bV\b", gu): t.append("👓 戴眼罩"); b += 0.05
    if "TT" in gu: t.append("👅 繫舌帶"); b += 0.05
    if "XB" in gu: t.append("🦺 交叉鼻箍")
    return t, b

def train_model():
    res = supabase.table("race_results").select("race_id,place_num,horse_code,jockey,trainer,actual_weight,draw,speed_mps").limit(5000).execute()
    df = pd.DataFrame(res.data)
    df["is_win"] = (df["place_num"] == 1).astype(int)
    df["pair"] = df["jockey"] + "_" + df["trainer"]
    p_dict = df.groupby("pair")["is_win"].mean().to_dict()
    j_dict = df.groupby("jockey")["is_win"].mean().to_dict()
    t_dict = df.groupby("trainer")["is_win"].mean().to_dict()
    s_dict = df.groupby("horse_code")["speed_mps"].mean().to_dict()
    g_spd = df["speed_mps"].mean() or 17.0

    df["speed"] = df["horse_code"].map(s_dict).fillna(g_spd)
    df["j_rt"] = df["jockey"].map(j_dict).fillna(0.08)
    df["t_rt"] = df["trainer"].map(t_dict).fillna(0.08)
    df["syn"] = df["pair"].map(p_dict).fillna(0.08)
    df["draw"] = df["draw"].fillna(7)
    df["weight"] = df["actual_weight"].fillna(120)

    means = df.groupby("race_id")[["weight", "speed"]].transform("mean")
    df["d_wt"] = df["weight"] - means["weight"]
    df["d_sp"] = df["speed"] - means["speed"]

    df["target"] = df["place_num"].map(lambda p: 3 if p==1 else (2 if p==2 else (1 if p==3 else 0)))
    cols = ["draw", "weight", "speed", "j_rt", "t_rt", "syn", "d_sp", "d_wt"]
    ranker = lgb.LGBMRanker(objective="lambdarank", n_estimators=60, learning_rate=0.05, random_state=42)
    ranker.fit(df[cols], df["target"], group=df.groupby("race_id", sort=False).size().values)
    return {"m": ranker, "c": cols, "j": j_dict, "t": t_dict, "p": p_dict, "s": s_dict, "gs": g_spd}

def fetch_race(date_str, v, r_no):
    url = f"[https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate=](https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate=){date_str}&Racecourse={v}&RaceNo={r_no}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code != 200 or "馬名" not in r.text: return None, []
    except Exception: return None, []

    soup = BeautifulSoup(r.text, "html.parser")
    dist_m = re.search(r"(\d{3,4})米", soup.text)
    dist = int(dist_m.group(1)) if dist_m else 1200
    track = "全天候" if "全天候" in soup.text else "草地"

    tbl, h_tr = None, None
    for t in soup.find_all("table"):
        for tr in t.find_all("tr"):
            if "馬名" in tr.text and ("評分" in tr.text or "負磅" in tr.text):
                tbl, h_tr = t, tr; break
        if tbl: break
    if not tbl: return None, []

    headers = [th.text.strip() for th in h_tr.find_all(["th", "td"])]
    idx = {}
    for i, h in enumerate(headers):
        hl = h.lower()
        if ("馬號" in h or "編號" in h or hl=="no.") and "no" not in idx: idx["no"] = i
        elif "馬名" in h and "name" not in idx: idx["name"] = i
        elif "負磅" in h and "+/-" not in h and "wt" not in idx: idx["wt"] = i
        elif "騎師" in h and "jk" not in idx: idx["jk"] = i
        elif "檔位" in h and "dr" not in idx: idx["dr"] = i
        elif "練馬師" in h and "tr" not in idx: idx["tr"] = i
        elif "評分" in h and "國際" not in h and "+/-" not in h and "rt" not in idx: idx["rt"] = i
        elif "配備" in h and "gr" not in idx: idx["gr"] = i

    horses = []
    passed = False
    for tr in tbl.find_all("tr"):
        if tr == h_tr: passed = True; continue
        if not passed: continue
        tds = [td.text.strip() for td in tr.find_all(["td", "th"])]
        if not tds or "no" not in idx or len(tds) <= idx["no"] or not tds[idx["no"]].isdigit(): continue

        raw_h = tds[idx["name"]] if "name" in idx else ""
        cm = re.search(r"\(([A-Z0-9]+)\)", raw_h)
        h_code = cm.group(1) if cm else f"H{tds[idx['no']]}"
        h_name = re.sub(r"[\s\xa0]*\(.*?\)", "", raw_h).strip()

        rt = int(re.findall(r"\d+", tds[idx["rt"]])[0]) if "rt" in idx and len(tds) > idx["rt"] and re.findall(r"\d+", tds[idx["rt"]]) else None
        gr = tds[idx["gr"]] if "gr" in idx and len(tds) > idx["gr"] else "-"
        if not gr or gr in ("--", "-"): gr = "-"
        tags, bonus = parse_gear(gr)

        horses.append({
            "no": int(tds[idx["no"]]), "name": h_name, "code": h_code,
            "wt": float(re.findall(r"\d+", tds[idx["wt"]])[0]) if "wt" in idx and re.findall(r"\d+", tds[idx["wt"]]) else 120.0,
            "jk": tds[idx["jk"]] if "jk" in idx else "",
            "dr": int(tds[idx["dr"]]) if "dr" in idx and tds[idx["dr"]].isdigit() else 7,
            "tr": tds[idx["tr"]] if "tr" in idx else "",
            "rt": rt, "gr": gr, "tags": tags, "bonus": bonus
        })
    return {"v": v, "dist": dist, "track": track}, horses

def run():
    target_date = "2026-09-27"
    date_hkjc = "2026/09/27"
    v = "ST"
    ctx = train_model()

    for r_no in range(1, 12):
        meta, horses = fetch_race(date_hkjc, v, r_no)
        if not meta or not horses: break
        r_id = f"{target_date.replace('-', '')}_{v}_{r_no:02d}"

        supabase.table("races").upsert({"race_id": r_id, "race_date": target_date, "venue": v, "race_no": r_no, "distance": meta["dist"], "track_type": meta["track"]}).execute()

        feats = []
        for h in horses:
            feats.append({
                "draw": h["dr"], "weight": h["wt"],
                "speed": ctx["s"].get(h["code"], ctx["gs"]),
                "j_rt": ctx["j"].get(h["jk"], 0.08),
                "t_rt": ctx["t"].get(h["tr"], 0.08),
                "syn": ctx["p"].get(f"{h['jk']}_{h['tr']}", 0.08)
            })
        fdf = pd.DataFrame(feats)
        fdf["d_wt"] = fdf["weight"] - fdf["weight"].mean()
        fdf["d_sp"] = fdf["speed"] - fdf["speed"].mean()

        scores = ctx["m"].predict(fdf[ctx["c"]])
        for i, h in enumerate(horses): scores[i] += h["bonus"]
        exp_s = np.exp(scores - np.max(scores))
        probs = (exp_s / exp_s.sum()) * 100.0

        for i, h in enumerate(horses):
            h["prob"] = round(float(probs[i]), 2)
            if h["dr"] <= 3: h["tags"].append("🎯 黃金內檔")
            elif h["dr"] >= 11: h["tags"].append("⚠️ 外檔考驗")

        horses.sort(key=lambda x: x["prob"], reverse=True)
        payload = []
        for rank, h in enumerate(horses, 1):
            payload.append({
                "race_id": r_id, "horse_no": h["no"], "horse_code": h["code"], "horse_name": h["name"],
                "draw": h["dr"], "jockey": h["jk"], "win_probability": h["prob"], "predicted_rank": rank,
                "rating": h["rt"], "gear": h["gr"], "smart_tags": h["tags"],
                "bet_strategy": "🎯 獨贏首選 / 連贏馬膽" if rank==1 else ("⚡ 次選主力" if rank==2 else ("🛡️ 連贏配腳" if rank<=4 else ""))
            })
        supabase.table("race_predictions").delete().eq("race_id", r_id).execute()
        supabase.table("race_predictions").insert(payload).execute()
        print(f"✓ 第 {r_no} 場更新完畢: {len(horses)} 匹馬")

if __name__ == "__main__":
    run()
