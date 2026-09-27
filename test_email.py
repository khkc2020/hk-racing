import os
import smtplib
from email.mime.text import MIMEText

EMAIL_TO = os.environ.get("EMAIL_TO")
EMAIL_USER = os.environ.get("EMAIL_USER")
EMAIL_PASS = os.environ.get("EMAIL_PASS")
EMAIL_HOST = os.environ.get("EMAIL_HOST")

print(f"正在連線至 {EMAIL_HOST}，發信帳號: {EMAIL_USER} ...")

msg = MIMEText("🎉 恭喜！香港賽馬 AI 系統的電郵推播功能已成功連線，往後賽事推薦與複盤將自動發送至此郵箱！", "plain", "utf-8")
msg["Subject"] = "🏇 【測試信】HKJC AI 電郵推播連線成功"
msg["From"] = EMAIL_USER
msg["To"] = EMAIL_TO

try:
    with smtplib.SMTP_SSL(EMAIL_HOST, 465) as s:
        s.login(EMAIL_USER, EMAIL_PASS)
        s.sendmail(EMAIL_USER, [EMAIL_TO], msg.as_string())
    print(f"✓ 測試信件已成功送出至 {EMAIL_TO}！")
except Exception as e:
    print(f"❌ 連線或登入失敗: {e}")
    raise e
