import os
import re
import time
import requests
import numpy as np
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from supabase import create_client

# ==============================================================================
# ⚡ 香港賽馬 AI：15秒極速「臨場排位變動 + 即時賠率 + 聰明錢落飛」同步引擎
# 專為賽前一日開盤後及賽日臨場設計：
# 1. 開盤守門員：賽前一日正午 12:00 前未開盤時，自動攔截舊賠率並清除誤報聰明錢標籤。
# 2. 排位動態同步：自動檢測當日退出馬 (Scratchings)、騎師臨時易配及最新排位體重。
# 3. 嚴格日期核對：東網鏡像強制核對賽日日期，100% 杜絕跨賽日舊賠率張冠李戴。
# ==============================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rxmkohhgznfcnhdqegwq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ4bWtvaGhnem5mY25oZHFlZ3dxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MDQ5OTk2OCwiZXhwIjoyMTA2MDc1OTY4fQ.QUqbXyvQuVuulKbiaI0jC20aUw21l1vd4pjXWEryjuI")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.hkjc.com/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

_SHARED_DRIVER = None

def is_odds_pool_open(target_date_str):
    """馬會固定於賽事前一日中午 12:00 (HKT) 開盤，11:30 AM 前不抓賠率、不開 Chrome"""
    try:
        hkt = timezone(timedelta(hours=8))
        now_hkt = datetime.now(hkt)
        race_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        open_dt = datetime(race_dt.year, race_dt.month, race_dt.day, 11, 30, tzinfo=hkt) - timedelta(days=1)
        close_dt = datetime(race_dt.year, race_dt.month, race_dt.day, 23, 55, tzinfo=hkt)
        return open_dt <= now_hkt <= close_dt
    except Exception:
        return True

def get_shared_driver():
    global _SHARED_DRIVER
    if _SHARED_DRIVER is not None:
        return _SHARED_DRIVER
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
        chrome_options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36")
        try:
            _SHARED_DRIVER = webdriver.Chrome(options=chrome_options)
        except Exception:
            from selenium.webdriver.chrome.service import Service
            from webdriver_manager.chrome import ChromeDriverManager
            _SHARED_DRIVER = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=chrome_options)
        return _SHARED_DRIVER
    except Exception:
        return None

def fetch_racecard_updates(target_date, venue, race_no):
    """快速檢查排位表最新變動：退出馬、臨時易配騎師、最新負磅"""
    date_slash = target_date.replace("-", "/")
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/RaceCard.aspx?RaceDate={date_slash}&Racecourse={venue}&RaceNo={race_no}"
    active_horses = {}
    scratched_nos = set()
    try:
        r = requests.get(url, headers=HEADERS, timeout=6)
        if r.status_code != 200:
            return active_horses, scratched_nos
        soup = BeautifulSoup(r.text, "html.parser")
        for tb in soup.find_all("table"):
            header_tr = tb.find("tr")
            if not header_tr:
                continue
            headers = [th.text.strip() for th in header_tr.find_all(["th", "td"])]
            h_idx = {}
            for i, h in enumerate(headers):
                if ("馬匹編號" in h or "馬號" in h) and "no" not in h_idx: h_idx["no"] = i
                elif "馬名" in h and "綵衣" not in h and "name" not in h_idx: h_idx["name"] = i
                elif "負磅" in h and "+/-" not in h and "wt" not in h_idx: h_idx["wt"] = i
                elif "騎師" in h and "jk" not in h_idx: h_idx["jk"] = i
                elif "練馬師" in h and "tr" not in h_idx: h_idx["tr"] = i
                elif "檔位" in h and "dr" not in h_idx: h_idx["dr"] = i
            if "no" not in h_idx or "name" not in h_idx:
                continue
            for tr in tb.find_all("tr")[1:]:
                tds = [td.text.strip() for td in tr.find_all(["td", "th"])]
                if len(tds) <= h_idx["no"] or not tds[h_idx["no"]].isdigit():
                    continue
                h_no = int(tds[h_idx["no"]])
                row_txt = " ".join(tds)
                if "退出" in row_txt or "Withdrawn" in row_txt or "SCR" in row_txt:
                    scratched_nos.add(h_no)
                    continue
                jk = tds[h_idx["jk"]] if "jk" in h_idx and len(tds) > h_idx["jk"] else ""
                tr_name = tds[h_idx["tr"]] if "tr" in h_idx and len(tds) > h_idx["tr"] else ""
                active_horses[h_no] = {
                    "jockey": f"{jk} / {tr_name}" if tr_name else jk
                }
    except Exception:
        pass
    return active_horses, scratched_nos

def fetch_live_odds_fast(target_date, venue, race_no):
    """三層極速賠率通道 (具備嚴格賽日核對，未開盤絕不誤抓舊盤)"""
    odds_map = {}
    if not is_odds_pool_open(target_date):
        return odds_map

    # 1. 馬會官方 getJSON.aspx
    urls = [
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=winplaodds&date={target_date}&venue={venue}&raceno={race_no}",
        f"https://bet.hkjc.com/racing/getJSON.aspx?type=win&date={target_date}&venue={venue}&raceno={race_no}"
    ]
    for u in urls:
        try:
            r = requests.get(u, headers=HEADERS, timeout=3.5)
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
                    return odds_map
        except Exception:
            pass

    # 2. 東網即時盤鏡像 (必須核對頁面日期與 target_date 一致！)
    url_oncc = f"https://racing.on.cc/racing/rat/current/rjratb{race_no:04d}x0.html"
    try:
        r = requests.get(url_oncc, headers=HEADERS, timeout=3.5)
        if r.status_code == 200 and r.text:
            r.encoding = "big5"
            soup = BeautifulSoup(r.text, "html.parser")
            page_txt = soup.get_text()
            parts_d = target_date.split("-")
            valid_date = True
            if len(parts_d) == 3:
                y_s, m_s, d_s = parts_d[0], str(int(parts_d[1])), str(int(parts_d[2]))
                d_pad, m_pad = parts_d[2], parts_d[1]
                valid_date = any(pat in page_txt for pat in [
                    f"{d_pad}/{m_pad}/{y_s}", f"{d_s}/{m_s}/{y_s}",
                    f"{y_s}年{m_s}月{d_s}日", f"{y_s}-{m_pad}-{d_pad}"
                ])
            if valid_date:
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
                    return odds_map
    except Exception:
        pass

    # 3. Selenium 單例共用視窗 (僅在已開盤時啟用)
    driver = get_shared_driver()
    if driver:
        try:
            url = f"https://bet.hkjc.com/ch/racing/wp/{target_date}/{venue}/{race_no}"
            driver.set_page_load_timeout(10)
            driver.get(url)
            time.sleep(1.2)
            soup = BeautifulSoup(driver.page_source, "html.parser")
            for tr in soup.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if tds and tds[0].isdigit():
                    h_no = int(tds[0])
                    nums = [float(re.sub(r"[^\d.]", "", x)) for x in tds[1:] if re.match(r"^\d+(\.\d+)?$", re.sub(r"[^\d.]", "", x))]
                    if nums:
                        win_val = nums[0] if len(nums) == 1 else nums[-2] if len(nums) >= 2 else None
                        pla_val = nums[-1] if len(nums) >= 2 else None
                        if win_val and 1.0 <= win_val <= 999.0:
                            odds_map[h_no] = {"win": win_val, "pla": pla_val}
        except Exception:
            pass

    return odds_map

def sync_live_odds_and_ev():
    print("=" * 65)
    print("⚡ 香港賽馬 AI：臨場排位變動、即時賠率與聰明錢秒刷引擎")
    print("=" * 65)

    r_res = sb.table("races").select("race_date, venue").order("race_date", desc=True).limit(1).execute()
    if not r_res.data:
        print("❌ 未在資料庫中找到賽事資料，請先執行一次排位預測。")
        return
    
    target_date = r_res.data[0]["race_date"]
    venue = r_res.data[0]["venue"]
    date_clean = target_date.replace("-", "")
    pool_open = is_odds_pool_open(target_date)
    print(f"🎯 目標賽日: {target_date} ({venue}) | 彩池狀態: {'🟢 已開盤' if pool_open else '⏸️ 未開盤 (賽前一日12:00開盤)'}")

    preds_res = sb.table("race_predictions").select("*").ilike("race_id", f"{date_clean}_{venue}%").execute()
    if not preds_res.data:
        print("❌ 尚未生成 AI 預測數據，請先運行完整排位預測。")
        return

    races_dict = {}
    for row in preds_res.data:
        races_dict.setdefault(row["race_id"], []).append(row)

    total_updated_horses = 0

    for race_id in sorted(races_dict.keys()):
        race_no = int(race_id.split("_")[-1])
        horses = races_dict[race_id]
        if not horses:
            continue

        # 1. 檢查排位表最新變動 (剔除退出馬、更新易配騎師)
        active_rc, scratched_nos = fetch_racecard_updates(target_date, venue, race_no)
        if scratched_nos:
            print(f"  [🚨 退出馬剔除] 第 {race_no} 場偵測到退出馬號: {sorted(list(scratched_nos))}")
            horses = [h for h in horses if h["horse_no"] not in scratched_nos]

        for h in horses:
            if h["horse_no"] in active_rc and active_rc[h["horse_no"]].get("jockey"):
                h["jockey"] = active_rc[h["horse_no"]]["jockey"]

        # 2. 若尚未開盤：清除誤存的舊賠率與假聰明錢標籤，重新歸一化勝率後入庫
        if not pool_open:
            raw_probs = np.array([float(h.get("win_probability") or 10.0) for h in horses])
            if raw_probs.sum() > 0:
                raw_probs = (raw_probs / raw_probs.sum()) * 100.0
            clean_unopened = []
            for i, h in enumerate(horses):
                tags = list(h.get("smart_tags") or [])
                # 移除所有與賠率、聰明錢、大戶位置、EV 相關的誤報標籤
                tags = [
                    t for t in tags
                    if not any(kw in str(t) for kw in ["位置賠率", "正期望值", "建議注碼", "聰明錢", "大戶位置", "終極黃金狙擊", "回飛"])
                ]
                w_p_val = round(float(raw_probs[i]), 2)
                fair_odd = round(min(99.0, max(1.5, 82.5 / max(w_p_val, 0.85))), 1)
                clean_unopened.append({
                    "race_id": race_id,
                    "horse_no": h["horse_no"],
                    "horse_code": h["horse_code"],
                    "horse_name": h["horse_name"],
                    "draw": h["draw"],
                    "jockey": h["jockey"],
                    "win_probability": w_p_val,
                    "predicted_rank": i + 1,
                    "is_value_bet": (i < 3),
                    "gear": h["gear"],
                    "rating": h["rating"],
                    "smart_tags": list(set(tags)),
                    "combo_synergy": 0.0,
                    "bet_strategy": "🎯 獨贏首選 / 實力馬膽" if i == 0 else ("⚡ 次選主力 / 黃金走位" if i == 1 else ("🛡️ 連贏配腳 / 高爆發冷門" if i < 4 else "")),
                    "market_odds": fair_odd,
                    "rest_days": h.get("rest_days") if h.get("rest_days") is not None else 21,
                    "fast_gallop_count": h.get("fast_gallop_count") if h.get("fast_gallop_count") else 3
                })
            clean_unopened.sort(key=lambda x: x["win_probability"], reverse=True)
            for idx_r, item in enumerate(clean_unopened, 1):
                item["predicted_rank"] = idx_r
                item["bet_strategy"] = "🎯 獨贏首選 / 實力馬膽" if idx_r == 1 else ("⚡ 次選主力 / 黃金走位" if idx_r == 2 else ("🛡️ 連贏配腳 / 高爆發冷門" if idx_r <= 4 else ""))
            sb.table("race_predictions").delete().eq("race_id", race_id).execute()
            sb.table("race_predictions").insert(clean_unopened).execute()
            total_updated_horses += len(clean_unopened)
            print(f"  ✓ 第 {race_no} 場 [未開盤淨化完成]: 已清除舊盤污染並同步最新排位 ({len(clean_unopened)} 匹馬)")
            continue

        # 3. 已開盤：抓取真實即時賠率
        odds_map = fetch_live_odds_fast(target_date, venue, race_no)
        if not odds_map:
            print(f"  • 第 {race_no} 場: 暫未抓到即時賠率")
            continue

        has_win = any(h["horse_no"] in odds_map and (odds_map[h["horse_no"]].get("win") or 0) > 1.0 for h in horses)
        if not has_win:
            continue

        win_implied = np.array([1.0 / max(float(odds_map.get(h["horse_no"], {}).get("win", 20.0) or 20.0), 1.01) for h in horses])
        win_mkt_probs = (win_implied / win_implied.sum()) * 100.0

        has_pla = any(h["horse_no"] in odds_map and (odds_map[h["horse_no"]].get("pla") or 0) > 1.0 for h in horses)
        if has_pla:
            pla_implied = np.array([1.0 / max(float(odds_map.get(h["horse_no"], {}).get("pla", 5.0) or 5.0), 1.01) for h in horses])
            pla_mkt_probs = (pla_implied / pla_implied.sum()) * 100.0
            mkt_composite = 0.70 * win_mkt_probs + 0.30 * pla_mkt_probs
        else:
            pla_mkt_probs = win_mkt_probs
            mkt_composite = win_mkt_probs

        raw_probs = np.array([float(h.get("win_probability") or 10.0) for h in horses])
        if raw_probs.sum() > 0:
            raw_probs = (raw_probs / raw_probs.sum()) * 100.0
        final_probs = 0.80 * raw_probs + 0.20 * mkt_composite

        updated_payload = []
        for i, h in enumerate(horses):
            h_no = h["horse_no"]
            odds_info = odds_map.get(h_no, {})
            win_odd = odds_info.get("win")
            pla_odd = odds_info.get("pla")

            tags = list(h.get("smart_tags") or [])
            tags = [t for t in tags if not t.startswith("位置賠率:") and not t.startswith("💎 正期望值") and not t.startswith("📊 建議注碼")]

            prev_win = h.get("market_odds")
            if win_odd and win_odd > 1.0 and prev_win and float(prev_win) > 1.0:
                drop_pct = ((float(prev_win) - win_odd) / float(prev_win)) * 100.0
                if drop_pct >= 20.0:
                    tags = [t for t in tags if "聰明錢落飛" not in t]
                    tags.append(f"🔥 聰明錢落飛 (-{int(drop_pct)}%)")
                elif drop_pct <= -25.0:
                    tags = [t for t in tags if "回飛" not in t]
                    tags.append(f"❄️ 資金冷卻回飛 (+{int(abs(drop_pct))}%)")

            w_p = win_mkt_probs[i]
            p_p = pla_mkt_probs[i]
            if win_odd and win_odd >= 7.0 and pla_odd and pla_odd > 1.0 and w_p > 0:
                if (p_p / w_p) >= 1.45 and not any("大戶位置鎖定" in t for t in tags):
                    tags.append("🎯 大戶位置鎖定 (假冷真熱)")

            p_model = final_probs[i] / 100.0
            ev_pct = 0.0
            is_val = False
            if win_odd and win_odd > 1.0:
                ev = (p_model * win_odd) - 1.0
                ev_pct = round(ev * 100.0, 1)
                if ev >= 0.08:
                    if win_odd <= 25.0 or p_model >= 0.04:
                        is_val = True
                        b = win_odd - 1.0
                        k = round(min(5.0, max(0.5, (ev / (4.0 * b)) * 100.0)), 1)
                        tags.append(f"💎 正期望值 EV: +{ev_pct}%")
                        tags.append(f"📊 建議注碼: 1/4 Kelly ({k}%)")

            if pla_odd:
                tags.append(f"位置賠率: {pla_odd}倍")

            safe_ev = round(max(-99.0, min(999.0, ev_pct)), 1)
            safe_odd = round(min(999.0, max(1.0, win_odd)), 1) if win_odd and win_odd > 1.0 else None
            safe_prob = round(float(min(100.0, max(0.0, final_probs[i]))), 2)

            updated_payload.append({
                "race_id": race_id,
                "horse_no": h_no,
                "horse_code": h["horse_code"],
                "horse_name": h["horse_name"],
                "draw": h["draw"],
                "jockey": h["jockey"],
                "win_probability": safe_prob,
                "predicted_rank": h.get("predicted_rank", 99),
                "is_value_bet": is_val or h.get("predicted_rank", 99) <= 3,
                "gear": h["gear"],
                "rating": h["rating"],
                "smart_tags": list(set(tags)),
                "combo_synergy": safe_ev,
                "bet_strategy": h.get("bet_strategy", ""),
                "market_odds": safe_odd,
                "rest_days": h.get("rest_days") if h.get("rest_days") is not None else 21,
                "fast_gallop_count": h.get("fast_gallop_count") if h.get("fast_gallop_count") else 3
            })

        updated_payload.sort(key=lambda x: x["win_probability"], reverse=True)
        for r_idx, item in enumerate(updated_payload, 1):
            item["predicted_rank"] = r_idx
            has_smart = any("聰明錢落飛" in t or "大戶位置鎖定" in t for t in item["smart_tags"])
            if r_idx <= 4 and item["combo_synergy"] >= 8.0 and has_smart:
                item["bet_strategy"] = f"👑 終極黃金狙擊 (EV +{item['combo_synergy']}%)"
            elif r_idx == 1:
                item["bet_strategy"] = "🎯 獨贏首選 / 實力馬膽"
            elif r_idx == 2:
                item["bet_strategy"] = "⚡ 次選主力 / 黃金走位"
            elif r_idx <= 4:
                item["bet_strategy"] = "🛡️ 連贏配腳 / 高爆發冷門"
            elif item["is_value_bet"]:
                item["bet_strategy"] = f"💎 價值突擊 (EV +{item['combo_synergy']}%)"

        sb.table("race_predictions").delete().eq("race_id", race_id).execute()
        clean_insert = [{k: v for k, v in row.items() if k != "place_odds"} for row in updated_payload]
        sb.table("race_predictions").insert(clean_insert).execute()
        
        top1 = updated_payload[0]
        total_updated_horses += len(updated_payload)
        print(f"  ✓ 第 {race_no} 場同步完成 (涵蓋 {len(updated_payload)} 匹馬，首選: {top1['horse_no']}號 {top1['horse_name']}, 賠率: {top1['market_odds']}, 勝率: {top1['win_probability']}%)")

    global _SHARED_DRIVER
    if _SHARED_DRIVER:
        try: _SHARED_DRIVER.quit()
        except Exception: pass
        _SHARED_DRIVER = None

    print(f"\n🎉 同步完畢！共處理 {total_updated_horses} 匹馬資料！")

if __name__ == "__main__":
    sync_live_odds_and_ev()
