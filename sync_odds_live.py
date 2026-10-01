import requests
from bs4 import BeautifulSoup
import re
from supabase import create_client

# 1. 資料庫連線配置
SUPABASE_URL = "https://rxmkohhgznfcnhdqegwq.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InJ4bWtvaGhnem5mY25oZHFlZ3dxIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTgyMzY5ODUsImV4cCI6MjA3MzgxMjk4NX0.U90iM3D5Q0Gcx5eO1qS5mE_B9W1lH4d-616sR-a1Fw8"

sb = create_client(SUPABASE_URL, SUPABASE_KEY)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}

def sync_all_odds():
    target_date = "2026-10-01"
    print("=== 開始極速同步東網/馬會即時獨贏賠率 ===")

    for race_no in range(1, 11):
        race_id = f"{target_date}_{race_no}"
        url = f"https://racing.on.cc/racing/rat/current/rjratb{race_no:04d}x0.html"
        
        try:
            r = requests.get(url, headers=HEADERS, timeout=6)
            r.encoding = "big5"
            soup = BeautifulSoup(r.text, "html.parser")
            
            updated_count = 0
            for tr in soup.find_all("tr"):
                tds = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
                if tds and tds[0].isdigit():
                    h_no = int(tds[0])
                    # 提取最後一個非空的獨贏賠率
                    nums = [float(x) for x in tds[2:] if re.match(r'^\d+(\.\d+)?$', x)]
                    if nums:
                        # 格式為 [WIN, PLA, WIN, PLA...]，倒數第2個為最新WIN賠率
                        win_odd = nums[-2] if (len(nums) >= 2 and len(nums) % 2 == 0) else nums[-1]
                        
                        # 更新 Supabase 該馬匹的 market_odds
                        sb.table("race_predictions").update({"market_odds": win_odd}).eq("race_id", race_id).eq("horse_no", h_no).execute()
                        updated_count += 1
                        
            print(f"  ✓ 第 {race_no} 場賠率同步完成（共更新 {updated_count} 匹馬）")
        except Exception as e:
            print(f"  ✗ 第 {race_no} 場抓取失敗: {e}")

    print("\n🎉 全部即時賠率已成功寫入 Supabase！請刷新手機網頁查看！")

if __name__ == "__main__":
    sync_all_odds()
