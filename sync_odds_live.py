import os
import re
import time
import requests
import numpy as np
from bs4 import BeautifulSoup
from supabase import create_client

# ==============================================================================
# ⚡ 香港賽馬 AI：15秒極速「臨場即時賠率 + 聰明錢落飛 + 正期望值 EV」秒刷引擎
# 專為開跑前臨場 5 分鐘大額資金敲擊設計：
# 1. 不爬排位、不爬晨操、不重複運算固定特徵，直接讀取資料庫現有 AI 實力預測。
# 2. 直連馬會官方數據流與東網實時盤口，3-5 秒抓完全場 10 場即時賠率。
# 3. 毫秒級運算 Odds Velocity (落飛幅度)、WP 背離度與 EV，即刻寫入 Supabase！
# ==============================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rxmkohhgznfcnhdqegwq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ4bWtvaGhnem5mY25oZHFlZ3dxIiwicm9sZSI6InNlcnZpY2Vfcm9sZSIsImlhdCI6MTc5MDQ5OTk2OCwiZXhwIjoyMTA2MDc1OTY4fQ.QUqbXyvQuVuulKbiaI0jC20aUw21l1vd4pjXWEryjuI")

sb = create_client(SUPABASE_URL, SUPABASE_KEY)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.on.cc/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

_SHARED_DRIVER = None

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

def fetch_live_odds_fast(target_date, venue, race_no):
    """三層極速賠率通道：1. 官方 getJSON 2. 東網即時鏡像 3. Selenium 單例"""
    odds_map = {}

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

    # 2. 東網即時盤鏡像
    url_oncc = f"https://racing.on.cc/racing/rat/current/rjratb{race_no:04d}x0.html"
    try:
        r = requests.get(url_oncc, headers=HEADERS, timeout=3.5)
        if r.status_code == 200 and r.text:
            r.encoding = "big5"
            soup = BeautifulSoup(r.text, "html.parser")
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

    # 3. Selenium 單例共用視窗
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
    print("⚡ 香港賽馬 AI：臨場秒級賠率與聰明錢同步引擎")
    print("=" * 65)

    # 1. 自動從資料庫獲取最新賽日
    r_res = sb.table("races").select("race_date, venue").order("race_date", desc=True).limit(1).execute()
    if not r_res.data:
        print("❌ 未在資料庫中找到賽事資料，請先執行一次排位預測。")
        return
    
    target_date = r_res.data[0]["race_date"]
    venue = r_res.data[0]["venue"]
    date_clean = target_date.replace("-", "")
    print(f"🎯 目標賽日: {target_date} ({venue})")

    # 獲取該賽日現有所有預測紀錄
    preds_res = sb.table("race_predictions").select("*").ilike("race_id", f"{date_clean}_{venue}%").execute()
    if not preds_res.data:
        print("❌ 尚未生成 AI 預測數據，請先運行完整排位預測。")
        return

    # 按場次分組
    races_dict = {}
    for row in preds_res.data:
        races_dict.setdefault(row["race_id"], []).append(row)

    print(f"✓ 載入 {len(races_dict)} 個場次預測，即時啟動秒刷...")

    total_updated_horses = 0

    for race_id in sorted(races_dict.keys()):
        race_no = int(race_id.split("_")[-1])
        horses = races_dict[race_id]
        if not horses:
            continue

        # 抓取即時賠率
        odds_map = fetch_live_odds_fast(target_date, venue, race_no)
        if not odds_map:
            print(f"  • 第 {race_no} 場: 暫未抓到即時賠率 (盤口可能未開或維護中)")
            continue

        # 計算場內市場勝率
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

        # 重新校準勝率 (80% 原始AI模型勝率 + 20% 理性市場資金權衡)
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
            # 清理過期賠率標籤
            tags = [t for t in tags if not t.startswith("位置賠率:") and not t.startswith("💎 正期望值") and not t.startswith("📊 建議注碼")]

            # 1. 賠率動量 (Odds Velocity)
            prev_win = h.get("market_odds")
            if win_odd and win_odd > 1.0 and prev_win and float(prev_win) > 1.0:
                drop_pct = ((float(prev_win) - win_odd) / float(prev_win)) * 100.0
                if drop_pct >= 20.0:
                    tags = [t for t in tags if "聰明錢落飛" not in t]
                    tags.append(f"🔥 聰明錢落飛 (-{int(drop_pct)}%)")
                elif drop_pct <= -25.0:
                    tags = [t for t in tags if "回飛" not in t]
                    tags.append(f"❄️ 資金冷卻回飛 (+{int(abs(drop_pct))}%)")

            # 2. WP 背離度
            w_p = win_mkt_probs[i]
            p_p = pla_mkt_probs[i]
            if win_odd and win_odd >= 7.0 and pla_odd and pla_odd > 1.0 and w_p > 0:
                if (p_p / w_p) >= 1.45 and not any("大戶位置鎖定" in t for t in tags):
                    tags.append("🎯 大戶位置鎖定 (假冷真熱)")

            # 3. 期望值與凱利
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
                "market_odds": safe_odd
            })

        # 按最新校準勝率重新排序
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

        # 寫入 Supabase
        sb.table("race_predictions").delete().eq("race_id", race_id).execute()
        clean_insert = [{k: v for k, v in row.items() if k != "place_odds"} for row in updated_payload]
        sb.table("race_predictions").insert(clean_insert).execute()
        
        top1 = updated_payload[0]
        total_updated_horses += len(updated_payload)
        print(f"  ✓ 第 {race_no} 場同步完成 (涵蓋 {len(updated_payload)} 匹馬，首選: {top1['horse_no']}號 {top1['horse_name']}, 賠率: {top1['market_odds']}, 勝率: {top1['win_probability']}%)")

    # 關閉 Selenium
    global _SHARED_DRIVER
    if _SHARED_DRIVER:
        try: _SHARED_DRIVER.quit()
        except Exception: pass
        _SHARED_DRIVER = None

    print(f"\n🎉 臨場秒刷完畢！共更新 {total_updated_horses} 匹馬即時賠率與聰明錢落飛，請刷新網頁查看！")

if __name__ == "__main__":
    sync_live_odds_and_ev()
