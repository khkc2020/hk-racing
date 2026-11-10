import os
import re
import json
import time
import requests
import numpy as np
from bs4 import BeautifulSoup
from supabase import create_client

# ==============================================================================
# 🏇 香港賽馬 AI：學術級「勝率量化預測 + 正期望值 (EV) + 1/4 Kelly 資金控管」全息引擎
# ==============================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rxmkohhgznfcnhdqegwq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.on.cc/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

LITERAL_LIVE_ODDS = {}

# 基礎實力底分 (Prior Tier)：差距收窄至 0.68 ~ 0.95，未列出之新客串騎師/練馬師預設給 0.72 (不再打入 0.40 冷宮)
BASE_JOCKEYS = {
    "潘頓": 0.95, "麥道朗": 0.93, "布文": 0.90, "何澤堯": 0.85,
    "艾兆禮": 0.83, "田泰安": 0.82, "周俊樂": 0.82, "霍宏聲": 0.80,
    "艾道拿": 0.79, "巴度": 0.78, "蔡明紹": 0.77, "梁家俊": 0.76,
    "班德禮": 0.76, "希威森": 0.75, "潘明輝": 0.75, "鍾易禮": 0.74,
    "奧璇壁": 0.76, "楊明綸": 0.72, "賀銘年": 0.72, "湯普新": 0.72,
    "巫顯東": 0.70, "陳嘉熙": 0.68, "黃智弘": 0.73, "黃寶妮": 0.70
}
DEFAULT_JOCKEY_PRIOR = 0.73  # 短期客串新騎師(如冬季外援)預設給中堅偏高底分

BASE_TRAINERS = {
    "蔡約翰": 0.90, "伍鵬志": 0.88, "呂健威": 0.87, "方嘉柏": 0.86,
    "沈集成": 0.86, "廖康銘": 0.85, "告東尼": 0.84, "大衛希斯": 0.84,
    "姚本輝": 0.83, "羅富全": 0.81, "蘇偉賢": 0.80, "巫偉傑": 0.80,
    "游達榮": 0.79, "葉楚航": 0.78, "文家良": 0.78, "賀賢": 0.78,
    "韋達": 0.77, "黎昭昇": 0.76, "容天鵬": 0.75, "徐雨石": 0.74,
    "丁冠豪": 0.74, "鄭俊偉": 0.73
}
DEFAULT_TRAINER_PRIOR = 0.75

def build_dynamic_jt_stats(sb_client):
    """
    從 Supabase race_results 自動抓取最近 600 條賽果，
    用貝氏平滑 (Bayesian Shrinkage) 計算每位騎師、練馬師及騎練配搭的實戰近期熱度！
    """
    j_stats = {}
    t_stats = {}
    pair_stats = {}
    try:
        if not sb_client:
            return j_stats, t_stats, pair_stats
        res = sb_client.table("race_results").select("jockey, trainer, place_num, placing").order("race_id", desc=True).limit(800).execute()
        for row in (res.data or []):
            raw_j = str(row.get("jockey") or "")
            clean_j = re.sub(r"\s*\(.*?\)", "", raw_j).strip()
            clean_t = str(row.get("trainer") or "").strip()
            pl_raw = row.get("place_num") if row.get("place_num") is not None else row.get("placing")
            try:
                pl = int(str(pl_raw).strip())
            except Exception:
                continue

            is_win = 1.0 if pl == 1 else 0.0
            is_top3 = 1.0 if 1 <= pl <= 3 else 0.0

            if clean_j:
                st = j_stats.setdefault(clean_j, {"runs": 0, "wins": 0.0, "top3": 0.0})
                st["runs"] += 1
                st["wins"] += is_win
                st["top3"] += is_top3
            if clean_t:
                st = t_stats.setdefault(clean_t, {"runs": 0, "wins": 0.0, "top3": 0.0})
                st["runs"] += 1
                st["wins"] += is_win
                st["top3"] += is_top3
            if clean_j and clean_t:
                pk = f"{clean_j}_{clean_t}"
                st = pair_stats.setdefault(pk, {"runs": 0, "wins": 0.0, "top3": 0.0})
                st["runs"] += 1
                st["wins"] += is_win
                st["top3"] += is_top3
    except Exception as e:
        print(f"  [提示] 動態騎練數據計算略過: {e}")
    return j_stats, t_stats, pair_stats

def get_fair_jt_score(clean_j, clean_t, j_stats, t_stats, pair_stats):
    """
    融合「基礎底分(45%) + 近期實戰勝率/上名率(55%) + 騎練合拍度」
    """
    tags = []
    # 1. 騎師分數 (貝氏平滑 K=12 場)
    j_prior = BASE_JOCKEYS.get(clean_j, DEFAULT_JOCKEY_PRIOR)
    j_rec = j_stats.get(clean_j, {"runs": 0, "wins": 0.0, "top3": 0.0})
    j_runs = j_rec["runs"]
    if j_runs > 0:
        # 標準上名率約 25%，勝率約 8.3%；換算成 0.50~1.00 評分尺
        perf_metric = ((j_rec["wins"] / j_runs) * 3.2) + ((j_rec["top3"] / j_runs) * 1.6)
        j_FormScore = min(0.98, max(0.55, 0.52 + perf_metric * 0.45))
        # 貝氏權重：跑夠 15 場以上主要由近期實戰決定，新客串騎師(j_runs少)由 Prior 保護
        w_form = j_runs / (j_runs + 12.0)
        j_final = (1.0 - w_form) * j_prior + w_form * j_FormScore
        if j_runs >= 8 and (j_rec["top3"] / j_runs) >= 0.35:
            tags.append(f"🔥 騎師近期手風大順 ({clean_j})")
    else:
        j_final = j_prior
        if clean_j and clean_j not in BASE_JOCKEYS:
            tags.append(f"🌍 生力軍/客串騎師 ({clean_j})")

    # 2. 練馬師分數 (貝氏平滑 K=15 場)
    t_prior = BASE_TRAINERS.get(clean_t, DEFAULT_TRAINER_PRIOR)
    t_rec = t_stats.get(clean_t, {"runs": 0, "wins": 0.0, "top3": 0.0})
    t_runs = t_rec["runs"]
    if t_runs > 0:
        t_metric = ((t_rec["wins"] / t_runs) * 3.2) + ((t_rec["top3"] / t_runs) * 1.6)
        t_FormScore = min(0.96, max(0.55, 0.52 + t_metric * 0.45))
        w_t_form = t_runs / (t_runs + 15.0)
        t_final = (1.0 - w_t_form) * t_prior + w_t_form * t_FormScore
        if t_runs >= 10 and (t_rec["top3"] / t_runs) >= 0.33:
            tags.append(f"🏠 馬房近期攻勢凌厲 ({clean_t})")
    else:
        t_final = t_prior

    # 3. 騎練合拍加成
    synergy_bonus = 0.0
    if clean_j and clean_t:
        pk = f"{clean_j}_{clean_t}"
        p_rec = pair_stats.get(pk, {"runs": 0, "wins": 0.0, "top3": 0.0})
        if p_rec["runs"] >= 4 and (p_rec["top3"] / p_rec["runs"]) >= 0.40:
            synergy_bonus = 0.08
            tags.append(f"🤝 騎練王牌合拍 ({clean_j}+{clean_t})")

    jt_score = ((j_final * 0.60 + t_final * 0.40) - 0.72) + synergy_bonus
    return jt_score, tags

_SHARED_SELENIUM_DRIVER = None

def get_shared_selenium_driver():
    global _SHARED_SELENIUM_DRIVER
    if _SHARED_SELENIUM_DRIVER is not None:
        return _SHARED_SELENIUM_DRIVER
    try:
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument("--disable-gpu")
        chrome_options.add_argument("--window-size=1920,1080")
        chrome_options.add_argument("--disable-extensions")
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        try:
            _SHARED_SELENIUM_DRIVER = webdriver.Chrome(options=chrome_options)
        except Exception:
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            _SHARED_SELENIUM_DRIVER = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        return _SHARED_SELENIUM_DRIVER
    except Exception as e:
        print(f"  [Selenium 初始化提示] {e}")
        return None

def close_shared_selenium_driver():
    global _SHARED_SELENIUM_DRIVER
    if _SHARED_SELENIUM_DRIVER:
        try:
            _SHARED_SELENIUM_DRIVER.quit()
        except Exception:
            pass
        _SHARED_SELENIUM_DRIVER = None

def safe_db_op(op_func, max_retries=4):
    for attempt in range(1, max_retries + 1):
        try:
            return op_func()
        except Exception as e:
            if attempt < max_retries:
                time.sleep(1.5 * attempt)
            else:
                print(f"Supabase 寫入異常重試失敗: {e}")
                raise e

def get_shatin_a_draw_score(distance, draw, course="A"):
    c_str = str(course).upper() if course else "A"
    is_narrow_rail = ("C" in c_str or "B+2" in c_str)
    if distance == 1000:
        if draw >= 10: score, tags = +0.20, ["🚀 直路看台外欄利位"]
        elif draw >= 7: score, tags = +0.08, []
        elif draw >= 4: score, tags = -0.05, []
        else: score, tags = -0.20, ["⚠️ 直路內欄吃風劣勢"]
        return score, tags
    elif distance == 1200:
        if 2 <= draw <= 5: score, tags = +0.25, ["🎯 短途黃金內欄"]
        elif draw == 1: score, tags = +0.15, ["🎯 1檔貼欄好位"]
        elif 6 <= draw <= 8: score, tags = +0.05, []
        elif 9 <= draw <= 10: score, tags = -0.10, []
        else: score, tags = -0.25, ["⚠️ 大外檔轉彎蝕位"]
    elif distance == 1400:
        if 1 <= draw <= 6: score, tags = +0.15, ["🎯 中內檔穩定佔先"]
        elif 7 <= draw <= 9: score, tags = 0.00, []
        else: score, tags = -0.15, []
    elif distance == 1600:
        if 3 <= draw <= 6: score, tags = +0.25, ["🎯 一哩出閘最佳順暢位"]
        elif 1 <= draw <= 2: score, tags = +0.08, ["貼欄但防關門"]
        elif 7 <= draw <= 9: score, tags = -0.05, []
        else: score, tags = -0.20, ["⚠️ 一哩急彎外疊大蝕"]
    else:
        if 1 <= draw <= 4: score, tags = +0.20, ["🎯 長途貼欄省體力"]
        elif 5 <= draw <= 8: score, tags = +0.05, []
        else: score, tags = -0.18, []
    if is_narrow_rail and distance > 1000:
        if draw == 2:
            score += 0.10
            tags.append(f"👑 田草{c_str}欄黃金2檔")
        elif 1 <= draw <= 5:
            score += 0.06
            tags.append(f"🚀 田草{c_str}欄內檔黃金跑線")
        elif draw >= 10:
            score -= 0.07
            tags.append(f"⚠️ 田草{c_str}欄外疊蝕腳程")
    return score, tags

def get_shatin_awt_draw_score(distance, draw):
    if distance == 1200:
        if 1 <= draw <= 5: return +0.22, ["🎯 泥地短途黃金內中檔 (先手防吃泥)"]
        elif 6 <= draw <= 8: return +0.05, []
        else: return -0.18, ["⚠️ 泥地短途外檔轉彎蝕位"]
    elif distance == 1650:
        if 1 <= draw <= 3: return +0.28, ["🎯 泥地一哩極短首彎頂級利位"]
        elif 4 <= draw <= 6: return +0.08, []
        elif 7 <= draw <= 9: return -0.10, ["⚠️ 一哩中外檔防被迫走外疊"]
        else: return -0.25, ["⚠️ 泥地一哩大外檔急彎大蝕位"]
    elif distance == 1800:
        if 1 <= draw <= 4: return +0.18, ["🎯 泥地長途貼欄省體力"]
        elif 5 <= draw <= 8: return +0.05, []
        else: return -0.12, []
    else:
        if 1 <= draw <= 4: return +0.15, ["🎯 泥地中內欄好位"]
        else: return -0.10, []

def get_happy_valley_draw_score(distance, draw, course="A"):
    score = 0.0
    tags = []
    c_str = str(course).upper() if course else "A"
    is_c_plus_3 = "C+3" in c_str or "C3" in c_str or "C" in c_str

    if distance == 1000:
        if 1 <= draw <= 4:
            score = +0.28
            tags.append("🎯 谷草短途黃金內欄先手")
        elif 5 <= draw <= 7:
            score = +0.05
        else:
            score = -0.22
            tags.append("⚠️ 谷草千米急彎外疊大蝕位")
    elif distance == 1200:
        if 1 <= draw <= 4:
            score = +0.25
            tags.append("🎯 谷草1200黃金內檔")
        elif 5 <= draw <= 8:
            score = +0.05
        else:
            score = -0.20
            tags.append("⚠️ 谷草1200外疊轉急彎蝕位")
    elif distance == 1650:
        if 1 <= draw <= 3:
            score = +0.32
            tags.append("🎯 谷草一哩首彎極短頂級利位")
        elif 4 <= draw <= 6:
            score = +0.10
        elif 7 <= draw <= 9:
            score = -0.12
            tags.append("⚠️ 谷草一哩防走外疊")
        else:
            score = -0.28
            tags.append("⚠️ 谷草一哩大外檔首彎大蝕位")
    elif distance == 1800:
        if 1 <= draw <= 4:
            score = +0.22
            tags.append("🎯 谷草千八貼欄省體力")
        elif 5 <= draw <= 8:
            score = +0.05
        else:
            score = -0.18
            tags.append("⚠️ 谷草千八外檔多轉急彎")
    else:
        if 1 <= draw <= 4:
            score = +0.20
            tags.append("🎯 谷草長途節省腳程")
        else:
            score = -0.15

    if is_c_plus_3:
        if 1 <= draw <= 3:
            score += 0.08
            tags.append("🚀 C+3 窄道極端內欄偏差利位")
        elif draw >= 9:
            score -= 0.08
            tags.append("⚠️ C+3 窄道外疊兜大圈極度不利")

    return score, list(set(tags))

def extract_trackwork_map(html_content, horses):
    if not html_content:
        return {}
    soup = BeautifulSoup(html_content, "html.parser")
    full_text = soup.get_text()
    horse_blocks = {}
    positions = []
    for h in horses:
        h_no = h["horse_no"]
        h_name = h["horse_name"]
        h_code = h.get("horse_code", "")
        patterns = [
            rf'(?:^|\n)\s*{h_no}\s+{re.escape(h_name)}',
            rf'{re.escape(h_name)}\s*[(（]{re.escape(h_code)}[)）]',
            rf'[(（]{re.escape(h_code)}[)）]',
            rf'(?:^|\n)\s*{re.escape(h_name)}\s+'
        ]
        best_pos = None
        for p in patterns:
            m = re.search(p, full_text)
            if m:
                best_pos = m.start()
                break
        if best_pos is not None:
            positions.append((best_pos, h_no))
    if not positions:
        return {}
    positions.sort(key=lambda x: x[0])
    for i, (pos, h_no) in enumerate(positions):
        end = positions[i+1][0] if i + 1 < len(positions) else len(full_text)
        horse_blocks[h_no] = full_text[pos:end].strip()
    return horse_blocks

def evaluate_trackwork(text, jockey_name, target_date_str=None, last_race_date_str=None):
    if not text: return 0.0, [], 2
    clean_j = re.sub(r"\s*\(.*?\)", "", jockey_name).strip()
    score = 0.0
    tags = []

    from datetime import datetime, date
    target_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date() if target_date_str else date.today()
    ref_year = target_dt.year
    last_dt = None
    if last_race_date_str:
        try:
            last_dt = datetime.strptime(last_race_date_str, "%Y-%m-%d").date()
        except Exception:
            last_dt = None

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    trial_records = []
    for line in lines:
        m_trial = re.search(r"第\d+組\d*\s+.*?[草地|全天候|泥地]\s*(\d+)/(\d+)\s*\((.*?)\)", line)
        if m_trial:
            rank = int(m_trial.group(1))
            total = int(m_trial.group(2))
            rider = m_trial.group(3).strip()
            m_date = re.search(r"(\d{2})/(\d{2})", line)
            trial_dt = None
            if m_date:
                d, m = int(m_date.group(1)), int(m_date.group(2))
                y = ref_year if target_dt.month >= m else ref_year - 1
                trial_dt = date(y, m, d)
            trial_records.append({
                "date": trial_dt,
                "rank": rank,
                "total": total,
                "rider": rider,
                "line": line
            })

    valid_trials = []
    for tr in trial_records:
        t_dt = tr["date"]
        if t_dt:
            if last_dt:
                if t_dt > last_dt and t_dt <= target_dt:
                    valid_trials.append(tr)
            else:
                if (target_dt - t_dt).days <= 28 and t_dt <= target_dt:
                    valid_trials.append(tr)
        else:
            valid_trials.append(tr)

    if valid_trials:
        valid_trials.sort(key=lambda x: x["date"] or date.min, reverse=True)
        latest_trial = valid_trials[0]
        rank = latest_trial["rank"]
        rider = latest_trial["rider"]
        line = latest_trial["line"]
        if rank == 1:
            score += 0.25
            tags.append("🔥 賽前試閘第1名")
        elif rank == 2:
            score += 0.18
            tags.append("⭐ 賽前試閘第2名")
        elif rank == 3:
            score += 0.10
            tags.append("✨ 賽前試閘第3名")
        elif rank >= 8:
            score -= 0.12
            tags.append("⚠️ 賽前試閘脫節大敗")

        if clean_j and clean_j in rider:
            score += 0.12
            tags.append(f"🏇 騎師親自試閘 ({clean_j})")

        pos_keywords = ["走勢輕鬆", "未見底", "自動湧上", "直路湧上", "扣實", "出腳爽朗", "步勁雄渾", "反應敏銳", "神態生猛", "火氣旺盛", "走勢順暢", "走勢良好"]
        neg_keywords = ["按韁無反應", "步頭笨重", "需要力策", "口勁過重", "轉彎外斜", "走勢生硬", "神色呆滯"]

        for kw in pos_keywords:
            if kw in line:
                score += 0.15
                tags.append(f"✨ 走勢評語: {kw}")
                break
        for kw in neg_keywords:
            if kw in line:
                score -= 0.15
                tags.append(f"⚠️ 走勢評語: {kw}")
                break

    gallops = re.findall(r"(\d{2})/(\d{2})[:\s]+.*?(?:沙田|從化|跑馬地|全天候|草地|泥地|快跳|拍跳|踱步).*?(\d{2}\.\d)?\s*(?:\((.*?)\))?", text)
    recent_gallops = 0
    for d_str, m_str, sec, rider in gallops:
        try:
            d_i, m_i = int(d_str), int(m_str)
            y_i = ref_year if target_dt.month >= m_i else ref_year - 1
            g_dt = date(y_i, m_i, d_i)
            if last_dt and g_dt <= last_dt:
                continue
            if 0 <= (target_dt - g_dt).days <= 28:
                recent_gallops += 1
                if clean_j and rider and clean_j in rider and not any("騎師親自" in t for t in tags):
                    score += 0.10
                    tags.append(f"🏇 賽前騎師親自快跳 ({clean_j})")
        except Exception:
            continue

    if recent_gallops == 0:
        sec_matches = re.findall(r"\b2[1-9]\.\d\b", text)
        recent_gallops = max(len(sec_matches), len(valid_trials))

    fast_gallop_count = recent_gallops + len(valid_trials)
    if fast_gallop_count >= 5:
        score += 0.08
        tags.append(f"💪 晨操備戰積極 ({fast_gallop_count}課)")
    elif fast_gallop_count < 2 and len(valid_trials) == 0:
        score -= 0.15
        tags.append("⚠️ 賽前備戰偏弱 (快跳不足)")

    return score, list(set(tags)), max(1, fast_gallop_count)

def fetch_trackwork_text(date_hkjc, venue, race_no):
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalTrackwork.aspx?RaceDate={date_hkjc}&Racecourse={venue}&RaceNo={race_no}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        if r.status_code == 200:
            return r.text
    except Exception:
        pass
    return ""

def evaluate_pace_and_style(h, past_record, past_runs_list=None, curr_dist=1200):
    runs_to_check = []
    if past_runs_list and isinstance(past_runs_list, list):
        runs_to_check = past_runs_list[:4]
    elif past_record and isinstance(past_record, dict):
        runs_to_check = [past_record]

    start_positions = []
    late_burst = False

    for idx, rec in enumerate(runs_to_check):
        pos_str = str(rec.get("running_position") or "").strip()
        if not pos_str:
            continue
        parts = [int(p) for p in pos_str.split() if p.isdigit()]
        if parts:
            s_pos = parts[0]
            f_pos = parts[-1]
            # 途程轉換修正：若上仗比今仗長(縮程)，預計早段位置會偏後；若增程則自動偏前
            try:
                last_d = int(rec.get("distance") or curr_dist)
            except Exception:
                last_d = curr_dist
            dist_shift = ((last_d - curr_dist) / 200.0) * 0.85
            adj_s_pos = max(1.0, float(s_pos) + dist_shift)
            start_positions.append(adj_s_pos)
            if idx <= 1 and s_pos >= 8 and (s_pos - f_pos >= 5 or f_pos <= 4):
                late_burst = True

    style = "MIDFIELD"
    if start_positions:
        sorted_starts = sorted(start_positions)
        rep_start = (sorted_starts[0] * 0.6 + float(np.median(start_positions)) * 0.4) if len(start_positions) >= 2 else float(start_positions[0])
        if rep_start <= 1.6 or sorted_starts[0] == 1:
            style = "LEADER"
        elif rep_start <= 4.3:
            style = "PROMINENT"
        elif rep_start <= 7.5:
            style = "MIDFIELD"
        else:
            style = "CLOSER"
    else:
        if h.get("draw", 7) <= 3 and any(g in str(h.get("gear", "")) for g in ["B", "V", "PC"]):
            style = "PROMINENT"

    return style, late_burst

def compute_harville_place_probs(win_probs_pct, num_places=3):
    p = np.clip(np.array(win_probs_pct, dtype=float) / 100.0, 1e-5, 0.999)
    p = p / p.sum()
    n = len(p)
    place_probs = np.zeros(n, dtype=float)
    for i in range(n):
        p1 = p[i]
        p2 = 0.0
        for j in range(n):
            if j == i:
                continue
            denom1 = max(1e-6, 1.0 - p[j])
            p2 += p[j] * (p[i] / denom1)
        p3 = 0.0
        if num_places >= 3 and n >= 3:
            for j in range(n):
                if j == i:
                    continue
                denom1 = max(1e-6, 1.0 - p[j])
                for k in range(n):
                    if k == i or k == j:
                        continue
                    denom2 = max(1e-6, 1.0 - p[j] - p[k])
                    p3 += p[j] * (p[k] / denom1) * (p[i] / denom2)
        place_probs[i] = (p1 + p2 + (p3 if num_places >= 3 else 0.0)) * 100.0
    return place_probs

def score_gear(gear_str, is_step_up_dist=False):
    score = 0.0
    tags = []
    if not gear_str or gear_str == "-":
        return 0.0, tags
    g = gear_str.upper()
    if re.search(r"B1|V1|PC1|P1", g):
        score += 0.20
        tags.append("👓 首次眼罩 (動殺機變革)")
    elif re.search(r"B2|V2", g):
        score += 0.10
        tags.append("👓 重戴眼罩 (刺激走勢)")
    elif re.search(r"B-|V-|PC-|P-", g):
        score += (0.14 if is_step_up_dist else 0.08)
        tags.append("🧘 初除眼罩 (放鬆留力爆末段)")
    elif re.search(r"\bB\b|\bV\b|\bPC\b", g):
        score += 0.05
        tags.append("👓 配戴眼罩")

    if "TT1" in g or "XB1" in g:
        score += 0.12
        tags.append("👅 首次舌帶 (呼吸暢順)")
    elif "TT" in g:
        score += 0.05
        tags.append("👅 繫舌帶")
    return score, tags

def score_body_weight(wt_diff_val):
    score = 0.0
    tags = []
    if wt_diff_val is None:
        return 0.0, tags
    diff = wt_diff_val
    if 5 <= diff <= 15:
        score += 0.12
        tags.append(f"💪 體力壯身 (+{diff}磅)")
    elif -8 <= diff <= 4:
        score += 0.05
        tags.append(f"⚖️ 體重平穩 ({diff:+d}磅)")
    elif diff >= 25:
        score -= 0.15
        tags.append(f"⚠️ 體重暴增 (+{diff}磅，肥態未收)")
    elif diff <= -18:
        score -= 0.18
        tags.append(f"⚠️ 體重驟降 ({diff}磅，體力透支)")
    return score, tags

def evaluate_form_and_transition(horse_code, curr_meta, curr_horse, form_str, avg_rating, sb_client, past_record=None, target_date=None):
    score = 0.0
    tags = []
    last_race_date = None
    rest_days = curr_horse.get("rest_days")
    rating = curr_horse["rating"]

    if form_str and form_str != "-":
        runs = [int(r.strip()) for r in form_str.split("/") if r.strip().isdigit()]
        if runs:
            last_run = runs[0]
            if last_run == 1:
                score += 0.25
                tags.append("👑 上仗勝出頭馬")
            elif last_run <= 3:
                score += 0.16
                tags.append("🥈 上仗入位前三")
            elif last_run <= 5:
                score += 0.06
                tags.append("📈 上仗跑近前五")
            elif last_run >= 10:
                score -= 0.10
                tags.append("⚠️ 上仗大敗")

            win_count = sum(1 for r in runs if r == 1)
            if win_count >= 2:
                score += 0.12
                tags.append("🔥 近期2捷以上")
    else:
        tags.append("🆕 初出新馬")

    if rating >= avg_rating + 5:
        score += 0.15
        tags.append("⭐ 班頂高分實力馬")

    try:
        last = past_record
        if not last and horse_code and sb_client:
            res = sb_client.table("race_results").select("*").eq("horse_code", horse_code).order("race_id", desc=True).limit(1).execute()
            if res.data:
                last = res.data[0]
                rid = str(last.get("race_id") or "")
                if not last.get("race_date") and len(rid) >= 8 and rid[:8].isdigit():
                    last["race_date"] = f"{rid[:4]}-{rid[4:6]}-{rid[6:8]}" 

        if last:
            last_race_date = last.get("race_date")
            inc_rep = str(last.get("incident_report") or "")
            trouble_kws = ["受困而未能望空", "未能望空", "勒避", "走外疊", "沒有遮擋", "無遮擋", "出閘笨拙", "碰撞", "失去平衡", "嚴重受阻", "受擠迫", "受阻礙", "難以望空", "被帶出外疊", "被迫走外疊"]
            if any(kw in inc_rep for kw in trouble_kws):
                try: p_num = int(last.get("place_num") or 99)
                except: p_num = 99
                if p_num <= 8:
                    score += 0.18
                    tags.append("🚧 上仗受阻/走外疊蝕位 (隱形實力馬)")

            l400 = last.get("last_400m_sec")
            if l400:
                try:
                    l400_val = float(l400)
                    if 21.0 <= l400_val <= 22.45:
                        score += 0.16
                        tags.append(f"⚡ 上仗末段極速 ({l400_val:.2f}s)")
                    elif 22.45 < l400_val <= 22.80:
                        score += 0.08
                        tags.append(f"⚡ 上仗尾速凌厲 ({l400_val:.2f}s)")
                except Exception:
                    pass

            if last_race_date and target_date:
                try:
                    from datetime import datetime
                    d_l = datetime.strptime(str(last_race_date), "%Y-%m-%d").date()
                    d_t = datetime.strptime(str(target_date), "%Y-%m-%d").date()
                    rd = (d_t - d_l).days
                    if rd > 0:
                        rest_days = rd
                except Exception:
                    pass
            if rest_days is None and last.get("rest_days") is not None:
                try: rest_days = int(last.get("rest_days"))
                except: pass

            if rest_days is not None:
                if 14 <= rest_days <= 35:
                    score += 0.08
                    tags.append(f"🔋 黃金休戰期 ({rest_days}日)")
                elif rest_days >= 90:
                    score -= 0.10
                    tags.append(f"⚠️ 久休復出 ({rest_days}日)")
                elif 0 < rest_days <= 10 and float(curr_horse.get("weight", 120)) >= 132:
                    score -= 0.08
                    tags.append(f"⚠️ 密跑連仗頂磅 ({rest_days}日)")

            last_d = last.get("distance")
            curr_d = curr_meta.get("distance", 1200)
            if last_d:
                if last_d == curr_d:
                    score += 0.12
                    tags.append(f"🎯 原程續戰 ({curr_d}米)")
                elif last_d > curr_d:
                    score += 0.15
                    tags.append(f"⚡ 縮程出擊 ({last_d}米 ➔ {curr_d}米)")
                else:
                    if last.get("place_num", 10) <= 4:
                        score += 0.12
                        tags.append(f"🚀 增程試準 ({last_d}米 ➔ {curr_d}米)")
                    else:
                        score -= 0.05
                        tags.append(f"⚠️ 初試增程 ({curr_d}米)")

            last_w = last.get("actual_weight")
            curr_w = float(curr_horse.get("weight", 125))
            if last_w:
                w_delta = curr_w - float(last_w)
                if w_delta <= -5:
                    score += 0.18
                    tags.append(f"🪶 相比上仗大幅減磅 ({w_delta:.0f}磅)")
                elif w_delta <= -2:
                    score += 0.08
                    tags.append(f"🪶 相比上仗減磅 ({w_delta:.0f}磅)")
                elif w_delta >= +5:
                    score -= 0.12
                    tags.append(f"⚖️ 相比上仗加磅 (+{w_delta:.0f}磅)")

            last_j = re.sub(r"\s*\(.*?\)", "", last.get("jockey", "")).strip()
            curr_j = re.sub(r"\s*\(.*?\)", "", curr_horse.get("jockey", "")).strip()
            if last_j and curr_j and last_j != curr_j:
                curr_j_score = BASE_JOCKEYS.get(curr_j, DEFAULT_JOCKEY_PRIOR)
                last_j_score = BASE_JOCKEYS.get(last_j, DEFAULT_JOCKEY_PRIOR)
                if curr_j_score >= 0.80 and curr_j_score > last_j_score:
                    score += 0.14
                    tags.append(f"⭐ 換強配騎師 ({last_j} ➔ {curr_j})")
                elif re.search(r"\(-(\d+)\)", curr_horse.get("jockey", "")):
                    claim = re.search(r"\(-(\d+)\)", curr_horse.get("jockey", "")).group(1)
                    score += 0.12
                    tags.append(f"🪶 換見習生實質減磅 (-{claim}磅)")
                else:
                    score += 0.05
                    tags.append(f"🔄 易配出擊 ({curr_j})")

            last_c = last.get("race_class", "")
            curr_c = curr_meta.get("race_class", "")
            if ("五班" in curr_c and "四班" in last_c) or ("四班" in curr_c and "三班" in last_c):
                score += 0.22
                tags.append("👑 降班出擊 (級數壓倒)")
            elif ("四班" in curr_c and "五班" in last_c) or ("三班" in curr_c and "四班" in last_c):
                score -= 0.10
                tags.append("⚠️ 升班挑戰")

            last_track = last.get("track_type", "")
            curr_track = curr_meta.get("track_type", "草地")
            if curr_track == "全天候跑道":
                if "全天候" in last_track or "泥地" in last_track:
                    if last.get("place_num", 10) <= 3:
                        score += 0.20
                        tags.append("🏜️ 泥地特佳 (上仗泥地入前三)")
                    elif last.get("place_num", 10) <= 5:
                        score += 0.10
                        tags.append("🎯 泥地實戰經驗佳")
                else:
                    tags.append("🔄 草地轉跑泥地 (適性考驗)")
    except Exception:
        pass

    return score, list(set(tags)), last_race_date, (rest_days if rest_days is not None else 0)

def detect_upcoming_meeting():
    url = "https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            m_url = re.search(r"racedate=(\d{4}/\d{2}/\d{2})&Racecourse=([A-Z0-9]+)", r.text, re.IGNORECASE)
            if m_url:
                d_slash = m_url.group(1)
                venue = m_url.group(2).upper()
                return d_slash.replace("/", "-"), d_slash, venue

            m_cn = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日.*?([沙田|跑馬地]+)", r.text)
            if m_cn:
                y, m, d = m_cn.group(1), int(m_cn.group(2)), int(m_cn.group(3))
                v_name = m_cn.group(4)
                venue = "ST" if "沙田" in v_name else "HV"
                return f"{y}-{m:02d}-{d:02d}", f"{y}/{m:02d}/{d:02d}", venue
    except Exception as e:
        print(f"自動探測賽期連線警示: {e}")

    return "2026-10-01", "2026/10/01", "ST"

def fetch_odds_via_selenium(target_date, venue, race_no):
    odds_map = {}
    driver = get_shared_selenium_driver()
    if not driver:
        return odds_map
    try:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC

        driver.set_page_load_timeout(15)
        url = f"https://bet.hkjc.com/ch/racing/wp/{target_date}/{venue}/{race_no}"
        driver.get(url)

        wait = WebDriverWait(driver, 6)
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "table")))
        time.sleep(1.5)

        soup = BeautifulSoup(driver.page_source, "html.parser")
        for tr in soup.find_all("tr"):
            tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
            if not tds:
                continue
            h_no_val = None
            if tds[0].isdigit():
                h_no_val = int(tds[0])
            
            if h_no_val and 1 <= h_no_val <= 14:
                nums = []
                for x in tds[1:]:
                    clean_x = re.sub(r"[^\d.]", "", x)
                    if re.match(r'^\d+(\.\d+)?$', clean_x):
                        val = float(clean_x)
                        if 1.0 <= val <= 999.0:
                            nums.append(val)
                if nums:
                    win_val = nums[0] if len(nums) == 1 else nums[-2] if len(nums) >= 2 else None
                    pla_val = nums[-1] if len(nums) >= 2 else None
                    odds_map[h_no_val] = {
                        "win": win_val,
                        "pla": pla_val
                    }
        if odds_map:
            print(f"  [Selenium] 第 {race_no} 場動態抓取成功: 共 {len(odds_map)} 匹馬 (含獨贏與位置賠率)")
            return odds_map
    except Exception as e:
        print(f"  [Selenium] 抓取提示: {e}，將切換至字面備用賠率機制")
    finally:
        pass
    return odds_map

def is_odds_pool_open(target_date_str):
    try:
        from datetime import datetime, timedelta, timezone
        hkt = timezone(timedelta(hours=8))
        now_hkt = datetime.now(hkt)
        race_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        open_dt = datetime(race_dt.year, race_dt.month, race_dt.day, 11, 30, tzinfo=hkt) - timedelta(days=1)
        close_dt = datetime(race_dt.year, race_dt.month, race_dt.day, 23, 55, tzinfo=hkt)
        return open_dt <= now_hkt <= close_dt
    except Exception:
        return True

def fetch_live_odds(race_no, target_date="2026-10-07", venue="HV"):
    odds_map = {}
    if not is_odds_pool_open(target_date):
        if race_no == 1:
            print(f"  [⏸️ 彩池未開盤] 賽事 ({target_date}) 將於賽前一日正午 12:00 開盤，跳過賠率抓取與 Chrome 啟動。")
        return {}

    urls = [
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=winplaodds&date={target_date}&venue={venue}&raceno={race_no}",
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=winplaodds&date={target_date}&venue={venue}&start={race_no}&end={race_no}",
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=win&date={target_date}&venue={venue}&raceno={race_no}"
    ]
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=6)
            if r.status_code == 200 and r.text and "=" in r.text:
                tokens = r.text.replace("&", ";").split(";")
                for tok in tokens:
                    if "=" in tok:
                        parts = tok.split("=")
                        if len(parts) == 2 and parts[0].strip().isdigit():
                            h_no = int(parts[0].strip())
                            vals = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", parts[1])]
                            if vals:
                                win_val = vals[0]
                                pla_val = vals[1] if len(vals) >= 2 else None
                                if 1.0 <= win_val <= 999.0:
                                    odds_map[h_no] = {"win": win_val, "pla": pla_val}
                if odds_map:
                    print(f"  [官方數據流] 第 {race_no} 場賠率獲取成功: 共 {len(odds_map)} 匹馬")
                    return odds_map
        except Exception:
            pass

    url_oncc = f"https://racing.on.cc/racing/rat/current/rjratb{race_no:04d}x0.html"
    try:
        r = requests.get(url_oncc, headers=HEADERS, timeout=6)
        if r.status_code == 200 and r.text:
            r.encoding = "big5"
            soup = BeautifulSoup(r.text, "html.parser")
            page_txt = soup.get_text()
            parts_d = target_date.split("-")
            if len(parts_d) == 3:
                y_s, m_s, d_s = parts_d[0], str(int(parts_d[1])), str(int(parts_d[2]))
                d_pad, m_pad = parts_d[2], parts_d[1]
                valid_date_OnPage = any(pat in page_txt for pat in [
                    f"{d_pad}/{m_pad}/{y_s}", f"{d_s}/{m_s}/{y_s}",
                    f"{y_s}年{m_s}月{d_s}日", f"{y_s}-{m_pad}-{d_pad}"
                ])
            else:
                valid_date_OnPage = True
            if not valid_date_OnPage:
                if race_no == 1:
                    print(f"  [⚠️ 東網防污染] 東網頁面仍停留在舊賽日，與目標賽日 {target_date} 不符，已自動攔截舊賠率！")
                soup = BeautifulSoup("", "html.parser")
            for tr in soup.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if tds and tds[0].isdigit():
                    h_no = int(tds[0])
                    nums = [float(x) for x in tds[2:] if re.match(r"^\d+(\.\d+)?$", x)]
                    if nums:
                        win_odd = nums[-2] if (len(nums) >= 2 and len(nums) % 2 == 0) else nums[-1]
                        pla_odd = nums[-1] if (len(nums) >= 2 and len(nums) % 2 == 0) else None
                        if 1.0 <= win_odd <= 999.0:
                            odds_map[h_no] = {"win": win_odd, "pla": pla_odd}
            if odds_map:
                print(f"  [東網即時盤] 第 {race_no} 場抓取成功: 共 {len(odds_map)} 匹馬")
                return odds_map
    except Exception:
        pass

    odds_map = fetch_odds_via_selenium(target_date, venue, race_no)
    if odds_map:
        return odds_map

    return odds_map

def fetch_race_horses(date_hkjc, venue, race_no):
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={date_hkjc}&Racecourse={venue}&RaceNo={race_no}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=12)
        if r.status_code != 200: return None, []
    except Exception as e:
        print(f"排位表連線異常 R{race_no}: {e}")
        return None, []

    soup = BeautifulSoup(r.text, "html.parser")
    race_text = soup.get_text()

    if "沒有相關賽事" in race_text or "賽事取消" in race_text:
        return None, []

    dist_m = re.search(r"(\d{3,4})\s*米", race_text)
    distance = int(dist_m.group(1)) if dist_m else 1200
    track = "草地" if "草地" in race_text else ("全天候跑道" if "全天候" in race_text else "草地")

    course_m = re.search(r'\"([A-C\+3]+)\"\s*賽道', race_text)
    course = course_m.group(1) if course_m else None

    race_class = "第四班"
    for c in ("第一班", "第二班", "第三班", "第四班", "第五班", "三級賽", "二級賽", "一級賽"):
        if c in race_text: race_class = c; break

    runners_map = {}
    for tb in soup.find_all("table"):
        header_tr = tb.find("tr")
        if not header_tr: continue
        headers = [th.text.strip() for th in header_tr.find_all(["th", "td"])]

        h_idx = {}
        for i, h in enumerate(headers):
            hl = h.lower().strip()
            if ("馬匹編號" in h or "馬號" in h or hl == "no.") and "horse_no" not in h_idx:
                h_idx["horse_no"] = i
            elif "馬名" in h and "綵衣" not in h and "horse_name" not in h_idx:
                h_idx["horse_name"] = i
            elif "烙號" in h and "horse_code" not in h_idx:
                h_idx["horse_code"] = i
            elif "負磅" in h and "+/-" not in h and "weight" not in h_idx:
                h_idx["weight"] = i
            elif "騎師" in h and "jockey" not in h_idx:
                h_idx["jockey"] = i
            elif "檔位" in h and "draw" not in h_idx:
                h_idx["draw"] = i
            elif "練馬師" in h and "trainer" not in h_idx:
                h_idx["trainer"] = i
            elif "評分" in h and "+/-" not in h and "rating" not in h_idx:
                h_idx["rating"] = i
            elif "配備" in h and "gear" not in h_idx:
                h_idx["gear"] = i
            elif ("排位體重" in h or "體重" in h) and "+/-" in h and "weight_diff" not in h_idx:
                h_idx["weight_diff"] = i
            elif "6次近績" in h or "近績" in h:
                h_idx["form"] = i
            elif ("距今日數" in h or "休息" in h or "日數" in h) and "rest_days" not in h_idx:
                h_idx["rest_days"] = i

        if "horse_no" not in h_idx or "horse_name" not in h_idx: continue

        for tr in tb.find_all("tr")[1:]:
            tds = [td.text.strip() for td in tr.find_all(["td", "th"])]
            if len(tds) <= h_idx["horse_no"]: continue
            val_no = tds[h_idx["horse_no"]]
            if not val_no.isdigit(): continue
            h_no = int(val_no)

            row_full_txt = " ".join(tds)
            if "退出" in row_full_txt or "Withdrawn" in row_full_txt or "SCR" in row_full_txt:
                continue
            raw_name = tds[h_idx["horse_name"]]
            clean_name = re.sub(r"\s*\(.*?\)", "", raw_name).strip()
            h_code = ""
            cm = re.search(r"\(([A-Z0-9]+)\)", raw_name)
            if cm: h_code = cm.group(1)
            elif "horse_code" in h_idx and len(tds) > h_idx["horse_code"]:
                h_code = tds[h_idx["horse_code"]].replace("(", "").replace(")", "").strip()

            wt = 120
            if "weight" in h_idx and len(tds) > h_idx["weight"]:
                w_m = re.search(r"\d+", tds[h_idx["weight"]])
                if w_m: wt = int(w_m.group(0))

            jk = tds[h_idx["jockey"]] if "jockey" in h_idx and len(tds) > h_idx["jockey"] else ""
            dr = 7
            if "draw" in h_idx and len(tds) > h_idx["draw"]:
                d_m = re.search(r"\d+", tds[h_idx["draw"]])
                if d_m: dr = int(d_m.group(0))

            tr_name = tds[h_idx["trainer"]] if "trainer" in h_idx and len(tds) > h_idx["trainer"] else ""
            rt = 40
            if "rating" in h_idx and len(tds) > h_idx["rating"]:
                r_m = re.search(r"\d+", tds[h_idx["rating"]])
                if r_m: rt = int(r_m.group(0))

            gr = tds[h_idx["gear"]] if "gear" in h_idx and len(tds) > h_idx["gear"] else "-"
            form_val = tds[h_idx["form"]] if "form" in h_idx and len(tds) > h_idx["form"] else "-"

            wd_val = None
            if "weight_diff" in h_idx and len(tds) > h_idx["weight_diff"]:
                wd_match = re.search(r"[-+]?\d+", tds[h_idx["weight_diff"]])
                if wd_match: wd_val = int(wd_match.group(0))

            rd_val = None
            if "rest_days" in h_idx and len(tds) > h_idx["rest_days"]:
                raw_rd = tds[h_idx["rest_days"]].strip()
                if raw_rd.isdigit():
                    rd_val = int(raw_rd)
                elif "初" in raw_rd or raw_rd == "-":
                    rd_val = 0

            if h_no not in runners_map:
                runners_map[h_no] = {
                    "horse_no": h_no, "horse_code": h_code, "horse_name": clean_name,
                    "weight": wt, "jockey": jk, "draw": dr, "trainer": tr_name,
                    "rating": rt, "gear": gr, "form": form_val, "weight_diff": wd_val,
                    "rest_days": rd_val
                }

    horses = [runners_map[k] for k in sorted(runners_map.keys())]
    meta = {
        "venue": venue, "distance": distance, "track_type": track,
        "course": course, "race_class": race_class
    }
    return meta, horses

def detect_intraday_track_bias(date_hkjc, venue):
    completed_list = []

    for r_no in range(1, 12):
        url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_hkjc}&Racecourse={venue}&RaceNo={r_no}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=6)
            if resp.status_code != 200 or "沒有相關資料" in resp.text:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            race_txt = soup.get_text()

            perf_tb = None
            for tb in soup.find_all("table"):
                txt_h = tb.get_text()
                if "名次" in txt_h and "馬名" in txt_h and "檔位" in txt_h:
                    perf_tb = tb
                    break
            if not perf_tb:
                break

            dist_m = re.search(r"(\d{3,4})\s*米", race_txt)
            r_dist = int(dist_m.group(1)) if dist_m else 1200
            is_awt = ("全天候" in race_txt or "泥地" in race_txt)
            is_st_str = (not is_awt and venue == "ST" and r_dist == 1000)
            cat = "AWT" if is_awt else ("TURF_STRAIGHT" if is_st_str else "TURF_TURN")

            headers = [th.get_text(strip=True) for th in perf_tb.find("tr").find_all(["td", "th"])]
            draw_idx = next((i for i, h in enumerate(headers) if "檔位" in h), 7)
            pos_idx = next((i for i, h in enumerate(headers) if "沿途走位" in h), 9)

            top_runners = []
            for tr in perf_tb.find_all("tr")[1:]:
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(tds) <= max(draw_idx, 2):
                    continue
                pl_str = tds[0].strip()
                if pl_str in ("1", "2", "3", "4", "01", "02", "03", "04"):
                    pl_num = int(pl_str)
                    pl_wt = 1.5 if pl_num == 1 else (1.2 if pl_num == 2 else (1.0 if pl_num == 3 else 0.8))
                    d_str = tds[draw_idx].strip()
                    d_val = int(d_str) if d_str.isdigit() else 7
                    start_pos = None
                    if len(tds) > pos_idx:
                        pos_parts = [int(x) for x in tds[pos_idx].split() if x.isdigit()]
                        if pos_parts:
                            start_pos = pos_parts[0]
                    top_runners.append({
                        "place": pl_num,
                        "weight": pl_wt,
                        "draw": d_val,
                        "start_pos": start_pos
                    })
            if len(top_runners) >= 3:
                completed_list.append({
                    "race_no": r_no,
                    "category": cat,
                    "distance": r_dist,
                    "runners": top_runners
                })
            else:
                break
        except Exception:
            break

    def _compute_cat_bias(cat_races):
        n_races = len(cat_races)
        if n_races == 0:
            return {
                "races": 0, "confidence": 0.0,
                "inside_boost": 0.0, "wide_boost": 0.0,
                "front_boost": 0.0, "closer_boost": 0.0,
                "summary": "待開跑"
            }
        conf = 0.38 if n_races == 1 else (0.72 if n_races == 2 else 1.00)
        inside_w, wide_w, front_w, closer_w, total_w = 0.0, 0.0, 0.0, 0.0, 0.0

        for idx, rc in enumerate(cat_races):
            recency_wt = 0.82 ** (n_races - 1 - idx)
            for rn in rc["runners"]:
                w = rn["weight"] * recency_wt
                total_w += w
                if rn["draw"] <= 6:
                    inside_w += w
                elif rn["draw"] >= 8:
                    wide_w += w
                if rn["start_pos"] is not None:
                    if rn["start_pos"] <= 4:
                        front_w += w
                    elif rn["start_pos"] >= 7:
                        closer_w += w

        in_ratio = inside_w / max(total_w, 1e-5)
        wi_ratio = wide_w / max(total_w, 1e-5)
        fr_ratio = front_w / max(total_w, 1e-5)
        cl_ratio = closer_w / max(total_w, 1e-5)

        in_boost, wi_boost, fr_boost, cl_boost = 0.0, 0.0, 0.0, 0.0
        notes = []

        if in_ratio >= 0.56:
            raw_b = min(0.12, 0.05 + (in_ratio - 0.56) * 0.25)
            in_boost = round(raw_b * conf, 3)
            notes.append(f"內中檔(1-6檔)佔比{int(in_ratio*100)}%(+{in_boost:.2f})")
        elif wi_ratio >= 0.50:
            raw_b = min(0.11, 0.05 + (wi_ratio - 0.50) * 0.22)
            wi_boost = round(raw_b * conf, 3)
            notes.append(f"中外疊(8檔+)佔比{int(wi_ratio*100)}%(+{wi_boost:.2f})")

        if fr_ratio >= 0.52:
            raw_b = min(0.12, 0.05 + (fr_ratio - 0.52) * 0.25)
            fr_boost = round(raw_b * conf, 3)
            notes.append(f"前置放頭(頭段前4)佔比{int(fr_ratio*100)}%(+{fr_boost:.2f})")
        elif cl_ratio >= 0.45:
            raw_b = min(0.12, 0.05 + (cl_ratio - 0.45) * 0.25)
            cl_boost = round(raw_b * conf, 3)
            notes.append(f"後追衝刺(頭段7位後)佔比{int(cl_ratio*100)}%(+{cl_boost:.2f})")

        return {
            "races": n_races,
            "confidence": conf,
            "inside_boost": in_boost,
            "wide_boost": wi_boost,
            "front_boost": fr_boost,
            "closer_boost": cl_boost,
            "summary": f"已跑{n_races}場(信度{int(conf*100)}%): " + (" | ".join(notes) if notes else "偏差均衡")
        }

    turf_turn_bias = _compute_cat_bias([x for x in completed_list if x["category"] == "TURF_TURN"])
    awt_bias = _compute_cat_bias([x for x in completed_list if x["category"] == "AWT"])
    straight_bias = _compute_cat_bias([x for x in completed_list if x["category"] == "TURF_STRAIGHT"])

    total_comp = len(completed_list)
    summary_parts = []
    if turf_turn_bias["races"] > 0:
        summary_parts.append(f"[草地彎道] {turf_turn_bias['summary']}")
    if awt_bias["races"] > 0:
        summary_parts.append(f"[泥地] {awt_bias['summary']}")
    if straight_bias["races"] > 0:
        summary_parts.append(f"[直路] {straight_bias['summary']}")

    return {
        "completed_races": total_comp,
        "TURF_TURN": turf_turn_bias,
        "AWT": awt_bias,
        "TURF_STRAIGHT": straight_bias,
        "inside_boost": turf_turn_bias["inside_boost"],
        "wide_boost": turf_turn_bias["wide_boost"],
        "front_boost": turf_turn_bias["front_boost"],
        "closer_boost": turf_turn_bias["closer_boost"],
        "summary": " ； ".join(summary_parts) if summary_parts else "尚未有已完成賽事"
    }

def run_upcoming():
    print("=== 🏇 香港賽馬 AI：學術級「勝率量化預測 + 正期望值 (EV) + 1/4 Kelly 資金控管」全息引擎 ===")
    target_date, date_hkjc, venue = detect_upcoming_meeting()
    intraday_bias = detect_intraday_track_bias(date_hkjc, venue)
    print(f"賽事日期: {target_date} ({venue}) | 實時賽道偏差偵測: {intraday_bias['summary']}")

    total_races = 0
    sb_master = create_client(SUPABASE_URL, SUPABASE_KEY)
    # 新增：開跑前自動統計全港騎師、練馬師近期實戰勝率與合拍度
    j_stats_map, t_stats_map, pair_stats_map = build_dynamic_jt_stats(sb_master)

    races_meta_cache = {}
    try:
        all_r = sb_master.table("races").select("race_id, race_date, distance, race_class, track_type, venue, course").limit(3000).execute()
        for r_row in (all_r.data or []):
            races_meta_cache[r_row["race_id"]] = r_row
    except Exception as e:
        print(f"  [提示] 載入 races 緩存: {e}")

    for race_no in range(1, 12):
        meta, horses = fetch_race_horses(date_hkjc, venue, race_no)
        if not meta or not horses:
            break

        total_races += 1
        race_id = f"{target_date.replace('-', '')}_{venue}_{race_no:02d}"

        odds_map = fetch_live_odds(race_no, target_date, venue)

        norm_odds_map = {}
        for k, v in (odds_map or {}).items():
            if isinstance(v, dict):
                norm_odds_map[k] = v
            elif isinstance(v, (int, float)):
                norm_odds_map[k] = {"win": float(v), "pla": None}
        odds_map = norm_odds_map

        prev_odds_map = {}
        prev_tags_map = {}
        try:
            prev_res = sb_master.table("race_predictions").select("horse_no, market_odds, smart_tags").eq("race_id", race_id).execute()
            for row in (prev_res.data or []):
                h_no_val = row.get("horse_no")
                w_val = row.get("market_odds")
                t_val = row.get("smart_tags") or []
                if h_no_val and w_val and float(w_val) > 1.0:
                    p_val = None
                    for t in (t_val if isinstance(t_val, list) else []):
                        if "位置賠率:" in str(t):
                            m_p = re.search(r"(\d+(?:\.\d+)?)", str(t))
                            if m_p: p_val = float(m_p.group(1))
                    prev_odds_map[h_no_val] = {
                        "win": float(w_val),
                        "pla": p_val
                    }
                    prev_tags_map[h_no_val] = t_val if isinstance(t_val, list) else []
        except Exception:
            pass

        pool_open = is_odds_pool_open(target_date)
        if not pool_open:
            odds_map = {}
            prev_odds_map = {}
            prev_tags_map = {}
        elif not any(v.get("win") for v in odds_map.values()) and prev_odds_map:
            print(f"  [🛡️ 賠率記憶保護] 偵測到馬會夜間維護，成功繼承資料庫現存賠率 (共 {len(prev_odds_map)} 匹馬)")
            odds_map = prev_odds_map

        tw_html = fetch_trackwork_text(date_hkjc, venue, race_no)
        tw_map = extract_trackwork_map(tw_html, horses)

        safe_db_op(lambda: create_client(SUPABASE_URL, SUPABASE_KEY).table("races").upsert({
            "race_id": race_id, "race_date": target_date,
            "venue": meta["venue"], "race_no": race_no, "distance": meta["distance"],
            "track_type": meta["track_type"], "course": meta["course"],
            "race_class": meta["race_class"]
        }).execute())

        avg_r = sum(float(h["rating"]) for h in horses) / len(horses) if horses else 40.0
        avg_w = sum(float(h["weight"]) for h in horses) / len(horses) if horses else 122.0
        dist = meta["distance"]

        horse_styles = {}
        horse_late_bursts = {}
        horse_past_cache = {}
        horse_codes_list = [h["horse_code"] for h in horses if h.get("horse_code")]
        batch_past_map = {}
        batch_history_map = {}
        try:
            if horse_codes_list and sb_master:
                r_batch = sb_master.table("race_results").select("*").in_("horse_code", horse_codes_list).order("race_id", desc=True).limit(len(horse_codes_list) * 6).execute()
                for row in (r_batch.data or []):
                    hc = row.get("horse_code")
                    rid = str(row.get("race_id") or "")
                    if hc and rid < race_id:
                        rm = races_meta_cache.get(rid, {})
                        if not row.get("race_date"):
                            if rm.get("race_date"):
                                row["race_date"] = rm["race_date"]
                            elif len(rid) >= 8 and rid[:8].isdigit():
                                row["race_date"] = f"{rid[:4]}-{rid[4:6]}-{rid[6:8]}"
                        if not row.get("distance") and rm.get("distance"):
                            row["distance"] = rm["distance"]
                        if not row.get("race_class") and rm.get("race_class"):
                            row["race_class"] = rm["race_class"]
                        if not row.get("track_type") and rm.get("track_type"):
                            row["track_type"] = rm["track_type"]
                        if not row.get("venue") and rm.get("venue"):
                            row["venue"] = rm["venue"]
                        if hc not in batch_past_map:
                            batch_past_map[hc] = row
                        batch_history_map.setdefault(hc, []).append(row)
        except Exception as e:
            print(f"  [提示] 批量查詢上仗賽果: {e}")

        for h in horses:
            h_code = h["horse_code"]
            h_no = h["horse_no"]
            past = batch_past_map.get(h_code)
            past_list = batch_history_map.get(h_code, [])
            horse_past_cache[h_no] = past
            st, lb = evaluate_pace_and_style(h, past, past_list, curr_dist=dist)
            horse_styles[h_no] = st
            horse_late_bursts[h_no] = lb

        num_leaders = sum(1 for s in horse_styles.values() if s == "LEADER")
        num_prominent = sum(1 for s in horse_styles.values() if s == "PROMINENT")
        is_solo_lead = (num_leaders <= 1)
        is_fast_pace = (num_leaders >= 3 or (num_leaders == 2 and num_prominent >= 4))

        win_mkt_probs_map = {}
        pla_mkt_probs_map = {}
        harville_pla_probs_map = {}
        num_places = 3 if len(horses) >= 7 else 2
        has_win_odds = any(h["horse_no"] in odds_map and (odds_map.get(h["horse_no"], {}).get("win") or 0) > 1.0 for h in horses)
        if has_win_odds:
            win_implied = np.array([1.0 / max(float(odds_map.get(h["horse_no"], {}).get("win", 20.0) or 20.0), 1.01) for h in horses])
            win_mkt_probs = (win_implied / win_implied.sum()) * 100.0
            harville_pla_probs = compute_harville_place_probs(win_mkt_probs, num_places=num_places)

            has_pla_odds = any(h["horse_no"] in odds_map and (odds_map.get(h["horse_no"], {}).get("pla") or 0) > 1.0 for h in horses)
            if has_pla_odds:
                pla_implied = np.array([1.0 / max(float(odds_map.get(h["horse_no"], {}).get("pla", 5.0) or 5.0), 1.01) for h in horses])
                pla_mkt_probs_300 = (pla_implied / pla_implied.sum()) * (num_places * 100.0)
                pla_mkt_probs_100 = pla_mkt_probs_300 / float(num_places)
                mkt_composite_probs = 0.75 * win_mkt_probs + 0.25 * pla_mkt_probs_100
            else:
                pla_mkt_probs_300 = harville_pla_probs
                mkt_composite_probs = win_mkt_probs

            for i_idx, h_item in enumerate(horses):
                win_mkt_probs_map[h_item["horse_no"]] = win_mkt_probs[i_idx]
                pla_mkt_probs_map[h_item["horse_no"]] = pla_mkt_probs_300[i_idx]
                harville_pla_probs_map[h_item["horse_no"]] = harville_pla_probs[i_idx]
        else:
            mkt_composite_probs = np.zeros(len(horses))

        scores = []
        fundamental_scores = []
        market_flow_bonuses = []
        tags_meta = {}
        for h in horses:
            h_no = h["horse_no"]
            h_tags = []

            s_form_trans, f_tags, last_race_date, calc_rest_days = evaluate_form_and_transition(
                h["horse_code"], meta, h, h["form"], avg_r, sb_master,
                past_record=horse_past_cache.get(h_no), target_date=target_date
            )
            h["rest_days"] = calc_rest_days
            r_scale = (float(h["rating"]) - avg_r) / 7.0
            pillar_1 = r_scale * 0.55 + s_form_trans * 0.45
            h_tags.extend(f_tags)

            venue = meta.get("venue", "ST")
            course = meta.get("course", "A") or "A"
            if venue == "HV" or "跑馬地" in str(venue) or "谷" in str(venue):
                d_score, d_tags = get_happy_valley_draw_score(dist, h["draw"], course)
            elif meta.get("track_type") == "全天候跑道":
                d_score, d_tags = get_shatin_awt_draw_score(dist, h["draw"])
            else:
                d_score, d_tags = get_shatin_a_draw_score(dist, h["draw"], course)
            pillar_2 = d_score
            h_tags.extend(d_tags)

            wt = float(h["weight"])
            if wt >= 134:
                w_score = -0.22
                h_tags.append("⚖️ 頂磅考驗")
            elif 124 <= wt <= 129:
                w_score = +0.18
                h_tags.append("⚡ 今日黃金負磅區")
            elif wt <= 123:
                w_score = +0.12
                h_tags.append("🪶 輕磅飛馳")
            else:
                w_score = 0.0

            m_claim = re.search(r"\(-(\d+)\)", h["jockey"])
            claim_bonus = 0.12 if m_claim else 0.0

            home_bonus = 0.0
            if (venue == "HV" or "跑馬地" in str(venue) or "谷" in str(venue)) and "方嘉柏" in h.get("trainer", ""):
                home_bonus = 0.15
                h_tags.append("👑 谷草王出擊 (方嘉柏主場)")

            clean_j = re.sub(r"\s*\(.*?\)", "", h["jockey"]).strip()
            clean_t = h.get("trainer", "").strip()
            jt_tier_score, jt_dyn_tags = get_fair_jt_score(clean_j, clean_t, j_stats_map, t_stats_map, pair_stats_map)
            h_tags.extend(jt_dyn_tags)

            loyalty_bonus = 0.0
            past_rec = horse_past_cache.get(h_no)
            if past_rec and clean_j:
                last_j = re.sub(r"\s*\(.*?\)", "", str(past_rec.get("jockey") or "")).strip()
                try:
                    last_pl_val = int(str(past_rec.get("place_num") or past_rec.get("placing") or 99).strip())
                except Exception:
                    last_pl_val = 99
                # 公平化：不論任何騎師，只要上仗跑入前5名且今仗原庄配搭連騎，一律給予連騎默契加分！
                if last_j == clean_j and (last_pl_val <= 5 or BASE_JOCKEYS.get(clean_j, 0.73) >= 0.80):
                    loyalty_bonus = 0.10
                    h_tags.append(f"🤝 原庄配搭連騎 ({clean_j})")

            pillar_3 = w_score * 0.40 + (claim_bonus + home_bonus) * 0.25 + (jt_tier_score + loyalty_bonus) * 0.35

            h_tw_text = tw_map.get(h_no, "")
            tw_score, tw_tags, fg_count = evaluate_trackwork(h_tw_text, h["jockey"], target_date, last_race_date)
            h["fast_gallop_count"] = fg_count
            pillar_4 = tw_score
            h_tags.extend(tw_tags)

            g_score, g_tags = score_gear(h["gear"])
            pillar_5 = g_score
            h_tags.extend(g_tags)

            wt_score, wt_tags = score_body_weight(h["weight_diff"])
            pillar_6 = wt_score
            h_tags.extend(wt_tags)

            h_style = horse_styles.get(h_no, "MIDFIELD")
            is_hv = (venue == "HV" or "跑馬地" in str(venue) or "谷" in str(venue))
            is_awt = (meta.get("track_type") == "全天候跑道" or "泥" in str(meta.get("track_type", "")))
            is_st_straight = (not is_hv and not is_awt and dist == 1000)
            is_c_rail = ("C" in str(course).upper() or "B+2" in str(course).upper())
            is_short_first_turn = (is_hv and dist in (1000, 1200, 1650)) or (is_awt and dist == 1650)

            if is_solo_lead:
                if h_style == "LEADER":
                    pace_bonus = +0.18
                    h_tags.append("⚡ 單騎領放 (步速形勢大好)")
                elif h_style == "PROMINENT":
                    pace_bonus = +0.08
                    h_tags.append("🎯 步速偏慢跟前利位")
                elif h_style == "CLOSER":
                    pace_bonus = -0.10
                    h_tags.append("⚠️ 慢步速戰略受制")
                else:
                    pace_bonus = 0.0
                    h_tags.append("🛡️ 居中守好位")
            elif is_fast_pace:
                if h_style == "LEADER":
                    pace_bonus = -0.12
                    h_tags.append("⚠️ 前段步速偏快 (慎防互搶力竭)")
                elif h_style == "CLOSER":
                    pace_bonus = +0.12 if is_hv else +0.18
                    h_tags.append("🦅 快步速得益 (有利後勁狂追)")
                    if not is_hv and not is_awt and dist >= 1400:
                        pace_bonus += 0.05
                        h_tags.append("🛣️ 田草長直路後追黃金舞台")
                elif h_style == "PROMINENT":
                    pace_bonus = -0.05
                    h_tags.append("🎯 擅長跟前好位")
                else:
                    pace_bonus = 0.05
                    h_tags.append("🛡️ 居中黃金遮擋")
            else:
                pace_bonus = 0.0
                if h_style == "LEADER":
                    h_tags.append("⚡ 擅長前領放頭")
                elif h_style == "PROMINENT":
                    h_tags.append("🎯 擅長跟前好位")
                elif h_style == "CLOSER":
                    h_tags.append("🦅 擅長後勁追趕")
                else:
                    h_tags.append("🛡️ 居中守好位")

            if is_awt:
                if h_style in ("LEADER", "PROMINENT") and h["draw"] <= 6:
                    pace_bonus += 0.08
                    h_tags.append("🏜️ 泥地前置免食泥頭優勢")
                elif h_style == "CLOSER" and not is_fast_pace:
                    pace_bonus -= 0.06
                    h_tags.append("⚠️ 泥地留後食泥頭劣勢")
            elif is_hv and is_c_rail:
                if h_style in ("LEADER", "PROMINENT") and h["draw"] <= 5:
                    pace_bonus += 0.08
                    h_tags.append(f"🏟️ 谷草{course}欄短直路前置優勢")
                elif h_style == "CLOSER" and not is_fast_pace:
                    pace_bonus -= 0.06
            elif not is_hv and not is_awt and is_c_rail:
                if dist >= 1600 and h_style in ("LEADER", "PROMINENT"):
                    pace_bonus += 0.14
                    h_tags.append(f"🏟️ 田草{course}欄中長途前置高速路")
                elif dist == 1200 and h_style in ("LEADER", "PROMINENT") and h["draw"] <= 6:
                    pace_bonus += 0.08
                    h_tags.append(f"🏟️ 田草{course}欄短途內中檔先手")
                elif dist >= 1600 and h_style == "CLOSER" and not is_fast_pace:
                    pace_bonus -= 0.08

            if is_c_rail and past_rec:
                last_rid = str(past_rec.get("race_id") or "")
                last_rm = races_meta_cache.get(last_rid, {})
                last_course = str(last_rm.get("course") or "")
                try:
                    last_pl = int(str(past_rec.get("placing") or "99").strip())
                except Exception:
                    last_pl = 99
                if ("C" in last_course.upper()) and 1 <= last_pl <= 3:
                    pace_bonus += 0.10
                    h_tags.append(f"🎯 {course}欄窄道上名專家")

            if not is_st_straight:
                inside_forward_cnt = sum(
                    1 for other_h in horses
                    if other_h["draw"] < h["draw"] and other_h["draw"] <= 8 and horse_styles.get(other_h["horse_no"]) in ("LEADER", "PROMINENT")
                )
                min_wide_draw = 8 if is_short_first_turn else 9
                min_inside_cnt = 2 if is_short_first_turn else 3
                if h_style in ("LEADER", "PROMINENT") and h["draw"] >= min_wide_draw and inside_forward_cnt >= min_inside_cnt:
                    if is_short_first_turn:
                        pace_bonus -= 0.14
                        h_tags.append("⚠️ 首彎極短外檔防被頂三疊")
                    else:
                        pace_bonus -= 0.08
                        h_tags.append("⚠️ 外檔搶前防被頂三疊")

            if horse_late_bursts.get(h_no):
                pillar_1 += 0.15
                h_tags.append("🚀 上仗末段狂追 (暗湧實力馬)")

            odds_info = odds_map.get(h_no, {})
            win_odd = odds_info.get("win")
            pla_odd = odds_info.get("pla")

            odds_velocity_bonus = 0.0
            prev_tags = prev_tags_map.get(h_no, [])
            prev_had_inflow = any("聰明錢落飛" in str(t) for t in prev_tags)

            if win_odd and win_odd > 1.0 and h_no in prev_odds_map:
                prev_win = prev_odds_map[h_no].get("win")
                if prev_win and prev_win > 1.0:
                    odds_drop_pct = ((prev_win - win_odd) / prev_win) * 100.0
                    if odds_drop_pct >= 20.0:
                        odds_velocity_bonus = +0.15
                        h_tags.append(f"🔥 聰明錢落飛 (-{int(odds_drop_pct)}%)")
                    elif prev_had_inflow and win_odd <= prev_win * 1.05:
                        odds_velocity_bonus = +0.15
                        existing_drop_tag = next((t for t in prev_tags if "聰明錢落飛" in str(t)), "🔥 聰明錢落飛")
                        h_tags.append(existing_drop_tag)
                    elif odds_drop_pct <= -25.0:
                        odds_velocity_bonus = -0.10
                        h_tags.append(f"❄️ 資金冷卻回飛 (+{int(abs(odds_drop_pct))}%)")
            elif prev_had_inflow:
                odds_velocity_bonus = +0.15
                existing_drop_tag = next((t for t in prev_tags if "聰明錢落飛" in str(t)), "🔥 聰明錢落飛")
                h_tags.append(existing_drop_tag)

            wp_divergence_bonus = 0.0
            p_prob_actual = pla_mkt_probs_map.get(h_no, 0.0)
            p_prob_harville = harville_pla_probs_map.get(h_no, 0.0)
            if win_odd and win_odd >= 7.0 and pla_odd and pla_odd > 1.0 and p_prob_harville > 0.0:
                wp_ratio = p_prob_actual / p_prob_harville
                if wp_ratio >= 1.25:
                    wp_divergence_bonus = +0.12
                    h_tags.append("🎯 大戶位置鎖定 (假冷真熱)")

            intraday_bonus = 0.0
            cat_key = "AWT" if is_awt else ("TURF_STRAIGHT" if is_st_straight else "TURF_TURN")
            active_bias = intraday_bias.get(cat_key) or {}
            if active_bias.get("races", 0) >= 1:
                conf_pct = int(active_bias.get("confidence", 0.38) * 100)
                dist_front_mult = 1.15 if dist <= 1200 else 1.0
                dist_closer_mult = 1.15 if dist >= 1400 else 1.0

                if not is_st_straight:
                    if active_bias.get("inside_boost", 0) > 0 and h["draw"] <= 6:
                        intraday_bonus += active_bias["inside_boost"]
                        h_tags.append(f"📡 AI實時偏差({conf_pct}%信度): 1-6內中檔利位")
                    elif active_bias.get("wide_boost", 0) > 0 and h["draw"] >= 8:
                        intraday_bonus += active_bias["wide_boost"]
                        h_tags.append(f"📡 AI實時偏差({conf_pct}%信度): 中外疊跑線有利")

                if active_bias.get("front_boost", 0) > 0 and h_style in ("LEADER", "PROMINENT"):
                    intraday_bonus += round(active_bias["front_boost"] * dist_front_mult, 3)
                    h_tags.append(f"📡 AI實時偏差({conf_pct}%信度): 場地利前置放頭")
                elif active_bias.get("closer_boost", 0) > 0 and h_style == "CLOSER":
                    intraday_bonus += round(active_bias["closer_boost"] * dist_closer_mult, 3)
                    h_tags.append(f"📡 AI實時偏差({conf_pct}%信度): 場地利後追衝刺")

            base_feature = (
                (pillar_1 * 0.25) + 
                (pillar_2 * 0.20) + 
                (pillar_3 * 0.20) + 
                (pillar_4 * 0.15) + 
                (pillar_5 * 0.10) + 
                (pillar_6 * 0.10)
            ) + pace_bonus + intraday_bonus
            mkt_flow = odds_velocity_bonus + wp_divergence_bonus
            total_feature = base_feature + mkt_flow

            fundamental_scores.append(base_feature)
            market_flow_bonuses.append(mkt_flow)
            scores.append(total_feature)
            tags_meta[h_no] = list(set(h_tags))

        fundamental_scores = np.array(fundamental_scores)
        exp_fund = np.exp(fundamental_scores * 2.2)
        fundamental_probs = (exp_fund / exp_fund.sum()) * 100.0

        scores = np.array(scores)
        exp_s = np.exp(scores * 2.2)
        raw_probs = (exp_s / exp_s.sum()) * 100.0

        if has_win_odds:
            safe_raw = np.clip(raw_probs, 1e-3, 100.0)
            safe_mkt = np.clip(mkt_composite_probs, 1e-3, 100.0)
            log_pooled = (safe_raw ** 0.80) * (safe_mkt ** 0.20)
            final_probs = (log_pooled / log_pooled.sum()) * 100.0
            # 防止賠率雙重計算(Double Counting)：計算 EV 採用 85% 純基本面實力 + 15% 資金動能，不摻入市場隱含賠率
            ev_base_probs = (0.85 * fundamental_probs) + (0.15 * raw_probs)
        else:
            final_probs = raw_probs
            ev_base_probs = fundamental_probs

        scored = []
        for i, h in enumerate(horses):
            h_no = h["horse_no"]
            odds_info = odds_map.get(h_no, {})
            win_odd = odds_info.get("win")
            pla_odd = odds_info.get("pla")

            tags = tags_meta.get(h_no, [])
            jockey_trainer_str = f"{h['jockey']} / {h['trainer']}" if h.get("trainer") else h["jockey"]

            # 使用純實力基準概率 p_ev_model 計算 EV 與 Kelly，避免落飛馬被重複吹捧
            p_ev_model = ev_base_probs[i] / 100.0
            p_model = final_probs[i] / 100.0
            is_val = False
            ev_pct = 0.0
            kelly_pct = 0.0

            if win_odd and win_odd > 1.0:
                ev = (p_ev_model * win_odd) - 1.0
                ev_pct = round(ev * 100.0, 1)
                if ev >= 0.08:
                    if win_odd <= 25.0 or p_ev_model >= 0.04:
                        is_val = True
                        b = win_odd - 1.0
                        kelly_1_4 = (ev / (4.0 * b)) * 100.0
                        kelly_pct = round(min(5.0, max(0.5, kelly_1_4)), 1)
                        tags.append(f"💎 正期望值 EV: +{ev_pct}%")
                        tags.append(f"📊 建議注碼: 1/4 Kelly ({kelly_pct}%)")

            if pla_odd:
                tags.append(f"位置賠率: {pla_odd}倍")

            safe_ev_pct = round(max(-99.0, min(999.0, ev_pct)), 1)
            safe_win_odd = round(min(999.0, max(1.0, win_odd)), 1) if win_odd and win_odd > 1.0 else None
            safe_win_prob = round(float(min(100.0, max(0.0, final_probs[i]))), 2)

            if safe_win_odd is None and safe_win_prob > 0:
                fair_odd = round(min(99.0, max(1.5, 82.5 / max(safe_win_prob, 0.85))), 1)
                db_market_odd = fair_odd
            else:
                db_market_odd = safe_win_odd

            scored.append({
                "race_id": race_id,
                "horse_no": h_no,
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": jockey_trainer_str,
                "weight": h["weight"],
                "win_probability": safe_win_prob,
                "gear": h["gear"],
                "rating": h["rating"],
                "smart_tags": list(set(tags)),
                "combo_synergy": safe_ev_pct,
                "market_odds": db_market_odd,
                "place_odds": pla_odd,
                "is_value_bet": is_val,
                "rest_days": h.get("rest_days", 0),
                "fast_gallop_count": h.get("fast_gallop_count", 2)
            })

        scored.sort(key=lambda x: x["win_probability"], reverse=True)
        final_payload = []
        for rank, item in enumerate(scored, 1):
            h_tags_list = item["smart_tags"]
            has_smart_money = any("聰明錢落飛" in str(t) or "大戶位置鎖定" in str(t) for t in h_tags_list)
            is_high_ev = (item["combo_synergy"] >= 8.0)

            if rank <= 4 and is_high_ev and has_smart_money:
                h_tags_list.append("👑 終極黃金狙擊 (基本面+EV+聰明錢共振)")
                strat = f"👑 終極黃金狙擊 (EV +{item['combo_synergy']}%)"
            elif rank == 1:
                strat = "🎯 獨贏首選 / 實力馬膽"
            elif rank == 2:
                strat = "⚡ 次選主力 / 黃金走位"
            elif rank <= 4:
                strat = "🛡️ 連贏配腳 / 高爆發冷門"
            elif item["is_value_bet"]:
                strat = f"💎 價值突擊 (EV +{item['combo_synergy']}%)"
            else:
                strat = ""

            item["smart_tags"] = list(set(h_tags_list))

            final_payload.append({
                "race_id": item["race_id"],
                "horse_no": item["horse_no"],
                "horse_code": item["horse_code"],
                "horse_name": item["horse_name"],
                "draw": item["draw"],
                "jockey": item["jockey"],
                "win_probability": item["win_probability"],
                "predicted_rank": rank,
                "is_value_bet": item["is_value_bet"] or (rank <= 3),
                "gear": item["gear"],
                "rating": item["rating"],
                "smart_tags": item["smart_tags"],
                "combo_synergy": item["combo_synergy"],
                "bet_strategy": strat,
                "market_odds": item["market_odds"],
                "place_odds": item["place_odds"],
                "rest_days": item.get("rest_days", 0),
                "fast_gallop_count": item.get("fast_gallop_count", 2)
            })

        def _write_preds():
            c = create_client(SUPABASE_URL, SUPABASE_KEY)
            c.table("race_predictions").delete().eq("race_id", race_id).execute()
            clean_payload = [{k: v for k, v in row.items() if k != "place_odds"} for row in final_payload]
            try:
                c.table("race_predictions").insert(clean_payload).execute()
            except Exception as e:
                err_s = str(e)
                for opt_col in ["fast_gallop_count", "rest_days", "market_odds"]:
                    if opt_col in err_s:
                        clean_payload = [{k: v for k, v in row.items() if k != opt_col} for row in clean_payload]
                c.table("race_predictions").insert(clean_payload).execute()
        safe_db_op(_write_preds)

        top_h = scored[0]
        odds_count = sum(1 for p in final_payload if p.get("market_odds") is not None)
        print(f"  ✓ 第 {race_no} 場完成 ({meta['distance']}米, 出賽: {len(horses)} 匹, 賠率涵蓋: {odds_count}匹, 首選: {top_h['horse_no']}號 {top_h['horse_name']} [{top_h['draw']}檔/{top_h['weight']}磅], 勝率:{top_h['win_probability']}%, 獨贏:{top_h['market_odds']}, 位置:{top_h['place_odds']})")

    close_shared_selenium_driver()
    print(f"\n🎉 成功！已完成專業評馬人六維全息綜合預測並全部寫入 Supabase！")

if __name__ == "__main__":
    try:
        run_upcoming()
    finally:
        close_shared_selenium_driver()
