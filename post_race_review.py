import os
import re
import time
import smtplib
import requests
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

EMAIL_TO = os.environ.get("EMAIL_TO", "bluecastlefc@yahoo.com.hk")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://racing.hkjc.com/",
}

def send_email(subject, html_content):
    if not EMAIL_USER or not EMAIL_PASS:
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"HKJC AI 複盤中心 <{EMAIL_USER}>"
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        with smtplib.SMTP_SSL(EMAIL_HOST, 465) as server:
            server.login(EMAIL_USER, EMAIL收到，為避免輸出過長超時，以下為簡潔核心版本：

---

### 1. GitHub Secrets 設定
前往 GitHub **Settings** -> **Secrets and variables** -> **Actions**，新增：
- `EMAIL_TO`: mouseben620@gmail.com
- `EMAIL_USER`: mouseben620@gmail.com
- `EMAIL_PASS`: Az92299873
- `EMAIL_HOST`: smtp.gmail.com

---

### 2. 賽後複盤腳本：`post_race_review.py`
在倉庫根目錄新建 `post_race_review.py`：

```python
import os, time, smtplib, requests, re
from bs4 import BeautifulSoup
from email.mime.text import MIMEText
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

EMAIL_TO = os.environ.get("EMAIL_TO", "bluecastlefc@yahoo.com.hk")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
EMAIL_HOST = os.environ.get("EMAIL_HOST", "smtp.gmail.com")

def send_email(subject, html):
    if not EMAIL_USER or not EMAIL_PASS:
        return
    msg = MIMEText(html, "html", "utf-8")
    msg["Subject"] = subject
    msg["From"] = f"HKJC AI 助手 <{EMAIL_USER}>"
    msg["To"] = EMAIL_TO
    with smtplib.SMTP_SSL(EMAIL_HOST, 465) as s:
        s.login(EMAIL_USER, EMAIL_PASS)
        s.sendmail(EMAIL_USER, [EMAIL_TO], msg.as_string())

def run_review():
    today = time.strftime("%Y-%m-%d")
    today_clean = today.replace("-", "")
    res = supabase.table("race_predictions").select("*").ilike("race_id", f"{today_clean}%").execute()
    if not res.data:
        return

    races = {}
    for r in res.data:
        races.setdefault(r["race_id"], []).append(r)

    total_races, win_hits, place_hits = len(races), 0, 0
    html = f"<h2>🏇 {today} 賽事 AI 命中率與 ROI 複盤</h2><ul>"

    for r_id, horses in sorted(races.items()):
        v = "ST" if "_ST_" in r_id else "HV"
        r_no = int(r_id.split("_")[-1])
        url = f"[https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate=](https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate=){today.replace('-', '/')}&Racecourse={v}&RaceNo={r_no}"
        try:
            resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            soup = BeautifulSoup(resp.text, "html.parser")
            tbl = soup.find("table", class_="f_tac")
            if not tbl: continue
            
            top3 = []
            for row in tbl.find_all("tr"):
                tds = [td.get_text().strip() for td in row.find_all("td")]
                if len(tds) > 3 and tds[0] in ("1", "2", "3"):
                    cm = re.search(r"\(([A-Z0-9]+)\)", tds[2])
                    if cm: top3.append((tds[0], cm.group(1)))

            horses.sort(key=lambda x: x.get("predicted_rank", 99))
            top_pick = horses[0]["horse_code"] if horses else None

            # 統計命中
            is_win = any(pos == "1" and code == top_pick for pos, code in top3)
            is_place = any(code == top_pick for _, code in top3)
            if is_win: win_hits += 1
            if is_place: place_hits += 1

            html += f"<li>第 {r_no} 場 - 頭選: {horses[0]['horse_name']} | 獨贏: {'✅ 命中' if is_win else '❌'} | 位置: {'✅ 命中' if is_place else '❌'}</li>"
        except Exception:
            continue

    win_rate = round((win_hits / total_races) * 100, 1) if total_races else 0
    place_rate = round((place_hits / total_races) * 100, 1) if total_races else 0
    html += f"</ul><h3>📊 總結：獨贏命中率 {win_rate}% | 前三名上名率 {place_rate}%</h3>"
    
    send_email(f"📈 【賽後戰報】{today} 賽馬 AI 複盤與命中統計", html)
    print("✓ 賽後複盤已發送至郵箱！")

if __name__ == "__main__":
    run_review()
