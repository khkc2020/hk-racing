import os
import re
import time
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from bs4 import BeautifulSoup
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
        print("⚠️ 未配置 EMAIL_USER 或 EMAIL_PASS，跳過電郵發送。")
        return
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = f"HKJC AI 量化助手 <{EMAIL_USER}>"
        msg["To"] = EMAIL_TO
        msg.attach(MIMEText(html_content, "html", "utf-8"))

        with smtplib.SMTP_SSL(EMAIL_HOST, 465) as server:
            server.login(EMAIL_USER, EMAIL_PASS)
            server.sendmail(EMAIL_USER, [EMAIL_TO], msg.as_string())
        print(f"✓ 已成功發送電郵至 {EMAIL_TO}！")
    except Exception as e:
        print(f"❌ 電郵發送失敗: {e}")

def get_live_odds(race_date_str, venue, race_no):
    # 抓取馬會官方即時獨贏賠率
    url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={race_date_str}&Racecourse={venue}&RaceNo={race_no}"
    # 亦可從即時賠率頁讀取
    odds_map = {}
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, "html.parser")
        tbl = soup.find("table", class_="f_tac") or soup.find("table", class_="tableBorder2")
        if tbl:
            for row in tbl.find_all("tr"):
                tds = [td.get_text().strip() for td in row.find_all("td")]
                if len(tds) >= 12 and tds[1].isdigit():
                    h_no = int(tds[1])
                    odds_str = tds[11].replace(",", "")
                    if re.match(r"^\d+(\.\d+)?$", odds_str):
                        odds_map[h_no] = float(odds_str)
    except Exception as e:
        print(f"  即時賠率讀取提示: {e}")
    return odds_map

def run_ev_check():
    today = time.strftime("%Y-%m-%d")
    today_clean = today.replace("-", "")
    print(f"=== 正在計算今日 ({today}) 即時賠率與正期望值 (+EV) ===")

    # 讀取今日所有預測
    res = supabase.table("race_predictions").select("*").ilike("race_id", f"{today_clean}%").execute()
    if not res.data:
        print("今日暫無預測數據。")
        return

    races = {}
    for p in res.data:
        r_id = p["race_id"]
        races.setdefault(r_id, []).append(p)

    ev_picks = []
    email_html = f"""
    <h2>🏇 香港賽馬 AI 今日精選與 +EV 超值推薦</h2>
    <p>日期：{today} | 接收郵箱：{EMAIL_TO}</p>
    <hr/>
    """

    for r_id, horses in sorted(races.items()):
        venue = "ST" if "_ST_" in r_id else "HV"
        r_no = int(r_id.split("_")[-1])
        odds_map = get_live_odds(today.replace("-", "/"), venue, r_no)

        horses.sort(key=lambda x: x.get("predicted_rank", 99))
        banker = horses[0] if horses else None

        email_html += f"<h3>📍 第 {r_no} 場 ({'沙田' if venue=='ST' else '跑馬地'})</h3>"
        if banker:
            email_html += f"<p>👑 <b>AI 頭選馬膽</b>：<b>{banker.get('horse_name')} ({banker.get('horse_code')})</b> | 檔位: {banker.get('draw')} | 勝率: {banker.get('win_probability')}%</p>"

        email_html += """
        <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;font-size:13px;width:100%;">
          <tr style="background:#f2f2f2;">
            <th>預測排名</th><th>馬號/馬名</th><th>檔位</th><th>騎師</th><th>勝率</th><th>即時賠率</th><th>期望值 (EV)</th><th>策略</th>
          </tr>
        """

        for h in horses:
            h_no = h.get("horse_no")
            odds = odds_map.get(h_no) or h.get("live_odds") or 0.0
            prob = (h.get("win_probability") or 0.0) / 100.0
            
            # EV = (勝率 * 賠率) - 1
            ev = round((prob * odds) - 1.0, 2) if odds > 0 else None
            is_val = ev is not None and ev >= 0.15

            if is_val:
                ev_picks.append((r_no, h.get("horse_name"), odds, ev))

            # 回寫 Supabase
            supabase.table("race_predictions").update({
                "live_odds": odds if odds > 0 else None,
                "expected_value": ev,
                "is_value_bet": is_val
            }).eq("race_id", r_id).eq("horse_code", h["horse_code"]).execute()

            ev_str = f"<b style='color:green;'>+{ev:.2f} (超值)</b>" if is_val else (f"{ev:.2f}" if ev is not None else "-")
            row_bg = "#e8f5e9" if is_val else "white"

            email_html += f"""
            <tr style="background:{row_bg};">
              <td align="center">{h.get('predicted_rank')}</td>
              <td><b>{h.get('horse_name')}</b> ({h.get('horse_code')})</td>
              <td align="center">{h.get('draw')}</td>
              <td>{h.get('jockey')}</td>
              <td align="center">{h.get('win_probability')}%</td>
              <td align="center">{odds if odds > 0 else '-'}</td>
              <td align="center">{ev_str}</td>
              <td>{h.get('bet_strategy') or ''}</td>
            </tr>
            """
        email_html += "</table><br/>"

    if ev_picks:
        email_html = f"<div style='background:#fff3cd;padding:10px;border-left:5px solid #ffc107;margin-bottom:15px;'><b>🔥 今日偵測到 {len(ev_picks)} 匹 +EV 價值爆發馬：</b><br/>" + \
                     "<br/>".join([f"• 第 {p[0]} 場: <b>{p[1]}</b> (賠率: {p[2]}倍 | EV: +{p[3]})" for p in ev_picks]) + "</div>" + email_html

    send_email(f"🏇 【賽前推薦】{today} 香港賽馬 AI 馬膽與 +EV 價值清單", email_html)

if __name__ == "__main__":
    run_ev_check()
