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
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={date_str}&Racecourse={v}&RaceNo={r_no}"
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
