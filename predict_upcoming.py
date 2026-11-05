
與 Gemini 對話
分析一下, 並睇睇有無野可以優化? 主要預測模型睇咩?

你上傳嘅壓縮檔（hk-racing-main.zip）目前喺系統解析中只顯示咗檔案目錄結構，而入面嘅原始碼內容全部變成咗二進制亂碼，所以我暫時睇唔到入面具體寫咗咩 Feature 同埋用咗邊種 Algorithm。

不過單憑檔案結構，已經可以睇得出你成個量化賽馬系統嘅架構脈絡，同時亦可以針對呢類架構提出幾個關鍵嘅優化方向：

1. 現有系統架構分析（根據檔案目錄）
成個 Project 採用咗好標準嘅 Serverless / GitHub Actions 自動化 Pipeline 架構：   
hk-racing-main (1).zip

數據回填與收集 (backfill_past_seasons.py / backfill_history.yml)：負責抓取往績數據建立歷史 Database。   
hk-racing-main (1).zip

賽前預測 (predict_upcoming.py / predict_cron.yml)：透過 Cron Job 定時執行，生成排位及賽事預測結果。   
hk-racing-main (1).zip

即時賠率同步 (sync_odds_live.py / sync_odds.yml)：喺開跑前定時拉取最新賠率（Win/Place Odds），用嚟捕捉臨場資金流向（Smart Money / 落飛）。   
hk-racing-main (1).zip

回測與賽後檢討 (backtest_current_model.py / post_race_review.py)：驗證模型表現、計算 ROI，並喺賽後對比預測與實際賽果。   
hk-racing-main (1).zip

前端展示 (index.html)：單頁式 Dashboard（估計係透過 GitHub Pages 或靜態託管展示預測結果同臨場數據）。   
hk-racing-main (1).zip

2. 架構層面嘅初步優化建議
GitHub Actions sync_odds.yml 嘅延遲問題：GitHub Actions 嘅 schedule (cron) 最密只可以每 5 分鐘行一次，而且喺繁忙時段經常會有幾分鐘至十幾分鐘嘅 Queue Delay。對於香港賽馬臨開跑前最後 3 至 5 分鐘嘅「極速落飛」或者賠率異動，GitHub Actions 好容易會 Miss 咗最關鍵嘅 Snapshot。建議臨場賠率呢部分可以考慮移去輕量級雲端（例如 Cloudflare Workers Cron、AWS Lambda 或本地 Script）做秒級/分鐘級抓取。

特徵工程與模型訓練分離：目錄入面有 predict_upcoming.py 同 backtest_current_model.py，但見唔到獨立嘅 features.py 或 model_train.py。如果特徵計算（Feature Engineering）嘅 Logic 同時重複寫喺 Backtest 同 Predict 兩邊，好容易會出現 Training-Serving Skew（回測同實戰計法有微小出入），亦都有機會喺 Backtest 唔小心引入 Data Leakage（用咗未來數據）。抽一個共用嘅 feature_pipeline.py 出嚟會穩陣好多。   
hk-racing-main (1).zip

3. 下一步：點樣俾我深入睇你個預測模型？
因為 .zip 壓縮檔內容無法直接讀取，如果你想我幫你詳細分析主要預測模型睇緊咩特徵（例如點樣計 Track Bias、檔位偏差、步速、配備變動、騎練動態等）同埋搵出 Code 入面可以優化嘅地方，你可以：

直接將 predict_upcoming.py 同埋 backtest_current_model.py 呢兩個核心 Python 檔（唔使壓縮成 zip）直接上傳上嚟；或者   
hk-racing-main (1).zip

直接 Copy & Paste predict_upcoming.py 入面計算 Score / 載入 Model 嘅核心 Code 貼過嚟。   
hk-racing-main (1).zip

你貼過嚟之後，我就可以直接同你拆解入面嘅權重邏輯、Feature 設計同埋潛在 Bug！

我可唔可以比file你? 唔zip?

你已停止此答覆
睇完你呢兩個核心 Python 檔（predict_upcoming.py 同 backtest_current_model.py），成個系統嘅邏輯非常清晰。你用咗一套「80% 專家規則特徵 (Rule-based Heuristics) + 20% 市場隱含概率 (Market Implied Probability)」嘅混合量化模型，再加埋 Softmax 場內歸一化、正期望值 (EV) 過濾同埋 1/4 Kelly 注碼控制。   
predict_upcoming.py
+ 3

以下為你拆解主要預測模型睇緊咩，然後直接指出 Code 入面極之關鍵嘅 Data Leakage（未來數據洩漏）同埋可以深度優化嘅地方。

一、 主要預測模型睇緊咩？（核心架構拆解）
喺 predict_upcoming.py 入面，每隻馬嘅基礎評分 total_feature 由 6 大基礎維度（加權 100%） 再加 4 個動態戰術/資金流外掛 Bonus 組成：   
predict_upcoming.py

1. 六大基礎實力維度 (Pillar 1 ~ 6)
維度	權重	主要睇咩指標與加扣分邏輯
Pillar 1：往績、班次與狀態	25%	
• 班內評分優勢 (55%)：(馬匹評分 - 同場平均評分) / 7.0 
predict_upcoming.py


• 上仗轉變與近況 (45%)：近 6 仗名次、升降班（降班 +0.22）、路程變動（縮程 +0.15）、負磅增減（減 5 磅以上 +0.18）、換大師傅/見習生、相隔日數（14-35日黃金期 +0.08）、競賽事件報告塞車/外疊受阻黑馬 (+0.18)、上仗末段 400m 極速 (<=22.45s 加 0.16) 
predict_upcoming.py

Pillar 2：跑道與檔位偏差	20%	
• 分開 沙田草地、沙田泥地 (AWT)、跑馬地 (HV) 三個獨立 Matrix。 
predict_upcoming.py


• 考慮埋移欄效應（例如谷草 C / C+3 欄 1-3 檔額外 +0.08；沙田窄欄 2 檔額外 +0.10；田草 1000m 直路外檔 10 檔以上 +0.20）。 
predict_upcoming.py

Pillar 3：騎練與負磅	20%	
• 負磅區間 (40%)：124-129 磅黃金負磅 +0.18，134 磅以上頂磅 -0.22。 
predict_upcoming.py


• 主場與減磅 (25%)：見習生減磅 +0.12、方嘉柏跑谷草主場 +0.15。 
predict_upcoming.py


• 頂級騎練與原庄配搭 (35%)：Hardcode 咗潘頓 (1.0)、麥道朗 (0.95)、蔡約翰 (0.92) 等 Tier 分數；上仗同一頂級騎師連騎再加 loyalty_bonus (+0.12)。 
predict_upcoming.py

Pillar 4：晨操與試閘	15%	
• 抓取馬會 LocalTrackwork 文字，過濾出上仗之後、今仗之前嘅試閘與快跳。 
predict_upcoming.py


• 試閘頭三名（+0.10 至 +0.25）、騎師親自試閘/快跳（+0.10 ~ +0.12）、正面/負面評語 Keyword（如「未見底」+0.15、「需要力策」-0.15）、總課數 >=5 課加分。 
predict_upcoming.py

Pillar 5：配備變動	10%	
• 首次戴眼罩/單邊眼罩 (B1/V1/PC1 加 0.20)、重戴 (B2/V2 加 0.10)、首次綁舌帶 (TT1/XB1 加 0.12)。 
predict_upcoming.py

Pillar 6：排位體重增減	10%	
• 體重增加 5~15 磅 (+0.12)、平穩 (+0.05)、暴增 >=25 磅 (-0.15)、驟降 <=-18 磅 (-0.18)。 
predict_upcoming.py

2. 四大動態戰術與資金流模組（直接加落 total_feature）
全場步速與跑法耦合 (pace_bonus)：統計同場有幾多隻 LEADER（放頭）同 PROMINENT（跟前）。如果全場得 <=1 隻放頭馬（單騎領放），放頭馬直接 +0.18；如果 >=3 隻放頭馬（快步速），後追馬 (CLOSER) 喺田草加 +0.18~0.23。同時計算埋「外檔前置馬入彎前有幾多隻內檔快馬頂住」（防被頂三疊懲罰 -0.08 ~ -0.14）。   
predict_upcoming.py
+ 2

賠率動量 (odds_velocity_bonus)：對比 Supabase 上一輪賠率，急跌 >=20% 視為「聰明錢落飛」(+0.15)，回飛 >=25% 扣 -0.10。   
predict_upcoming.py

獨贏/位置背離度 (wp_divergence_bonus)：獨贏 7 倍以上，但位置隱含概率比獨贏高出 45% 以上（大戶買位置保本伏兵），加 +0.12。   
predict_upcoming.py

同日實時跑道偏差 (intraday_bonus)：自動爬當日已跑完場次嘅頭三名檔位同走位，如果當日內檔或前置馬佔比超過 55%~60%，後面場次自動動態加 +0.07 ~ +0.08。   
predict_upcoming.py

3. 概率轉換與注碼決策
Softmax 歸一化：exp(scores * 2.2) / sum() 轉成加總 100% 嘅 raw_probs。   
predict_upcoming.py

市場融合：final_probs = 0.80 * raw_probs + 0.20 * (0.7 * 獨贏去水勝率 + 0.3 * 位置去水率)。   
predict_upcoming.py

EV 與 Kelly：當 EV = P_model * Win_Odds - 1 >= +8%（且賠率 <=25 倍或勝率 >=4%），觸發價值投注，並用 1/4 Kelly 控制注碼喺本金 0.5% ~ 5.0% 之間。   
predict_upcoming.py

二、 關鍵問題與優化建議（由最嚴重到進階）
🚨 1. backtest_current_model.py 存在嚴重嘅「未來數據洩漏 (Data Leakage)」！
呢個係目前最致命嘅 Bug，會令你個回測勝率同 ROI 嚴重虛高（假象）：

問題 A（用咗今場嘅沿途走位預測今場）：
喺 backtest_current_model.py 第 272 行：   
backtest_current_model.py

Python
st, lb = evaluate_pace_and_style(r.get("running_position"), dr, r.get("gear"))
喺回測入面，r 係當場賽事嘅賽果 (race_results)！你直接將該場馬匹跑完之後嘅 running_position（例如沿途走位 10 9 2，即係後追衝上第 2 名）傳入去計 late_burst！
因為 evaluate_pace_and_style 入面寫咗：if start_pos >= 8 and (start_pos - final_pos >= 5 or final_pos <= 5): late_burst = True，然後第 337 行直接俾咗 late_burst_bonus = 0.15！
換言之：喺回測入面，邊隻馬喺該場比賽留後追入前五名，模型就喺賽前「預知未來」幫佢加 +0.15 分！ 同理，該場出閘搶到第 1 位嘅馬，回測亦直接當佢係 LEADER。   
backtest_current_model.py
+ 2

修正方法：喺 backtest_current_model.py 入面，每隻馬嘅 running_position、上仗名次、上仗負磅等特徵，必須取自 horse_runs 入面早於當前 race_id 嘅上一場紀錄 (prev_run)，絕對唔可以讀當前 r 嘅 running_position！

⚠️ 2. 回測模型 (backtest) 同實戰模型 (predict) 嚴重不一致 (Training-Serving Skew)
你喺 backtest_current_model.py 驗證緊嘅公式，同 predict_upcoming.py 實戰跑緊嘅公式有好大出入：

權重同維度唔同：

predict_upcoming.py 係 6 個 Pillar (0.25, 0.20, 0.20, 0.15, 0.10, 0.10)，騎練放喺 Pillar 3 入面佔 0.20 * 0.35 = 7%。   
predict_upcoming.py

backtest_current_model.py 係 5 個 Pillar (0.25, 0.20, 0.20, 0.15, 0.20)，騎練獨立做咗 p5 佔咗 20% 權重！   
backtest_current_model.py

新特徵寫咗計算但無放落回測評分：

backtest_current_model.py 標題話納入咗 last_400m_sec 同 incident_report，Step 2 亦都好俾心機計咗 calc_last_400m，但去到 Step 4（第 282-340 行）計 curr_feature 嗰陣，完全無用到 calc_last_400m、無用到 incident_report、亦無計升降班同負磅增減！   
backtest_current_model.py
+ 1

⚠️ 3. last_400m_sec 嘅估算公式並非真實末段時間
喺 backtest_current_model.py 第 172-182 行，因為資料庫原本無真實嘅末段 400m 時間，你用咗頭馬總時間除段數，再用 pos_gained * 0.13 同 time_gap * 0.22 去「人工推算」末段 400m 秒數。   
backtest_current_model.py

問題：如果一場慢步速賽事，頭馬完成時間慢，你條公式計出嚟全場馬嘅 base_final_400m 都會變慢；但現實中慢步速賽事嘅末段 400m 往往係極快（鬥爆末段 21.8s）！用總時間反推末段時間喺慢步速同快步速下會完全顛倒。

優化建議：既然你有爬馬會賽果，不如喺 backfill 階段直接爬馬會賽果頁面或分段時間表（Sectional Times）入面真實嘅最後 400m 時間，而唔好靠總時間去估。

三、 預測模型與數學邏輯優化建議
1. 動態調整「市場隱含概率 (20%)」嘅權重（依開飛時間動態變化）
目前你固定用 0.80 * raw_probs + 0.20 * mkt_composite_probs。   
predict_upcoming.py

問題：前一日下晝剛開飛嗰陣，彩池總投注額極少，賠率充滿雜訊；但去到開跑前 3 分鐘，彩池有過億資金，市場賠率嘅資訊含量（Efficiency）極高。相反，如果將 20% 市場賠率混入 final_probs，再用 final_probs 乘以 win_odd 去計 EV，其實會稀釋咗你自己模型嘅獨立觀點。

優化：

計真實勝率 (win_probability 顯示俾自己睇)：可以用 65% 模型 + 35% 市場（甚至按距離開跑時間動態調整）。

計 Value Bet (EV)：應該比較 純模型勝率 (raw_probs) 同 去水後市場勝率 (win_mkt_probs) 之間嘅差距（或者用 Log-linear pooling: 
P∝P 
model
α
​
 ⋅P 
mkt
1−α
​
 ），而唔係線性相加。線性相加會令大冷門（例如模型睇 5%，市場睇 1%）夾硬拉高，導致假正期望值（False Positive EV）。

2. 位置賠率歸一化 (pla_mkt_probs) 嘅除數錯誤
喺 predict_upcoming.py 第 686 行：   
predict_upcoming.py

Python
pla_implied = np.array([1.0 / max(float(odds_map.get(h["horse_no"], {}).get("pla", 5.0) or 5.0), 1.01) for h in horses])
pla_mkt_probs = (pla_implied / pla_implied.sum()) * 100.0
數學問題：獨贏只得 1 隻馬贏，所以所有馬嘅獨贏勝率加埋係 100%；但位置（Place）有 3 個名額（或少於 7 匹馬時 2 個名額），所有馬入三甲嘅真實概率加總應該係 300%！

你將 pla_implied 除以 sum() 歸一化到 100%，再喺第 792 行用 wp_ratio = p_prob / w_prob >= 1.45 去捉「假冷真熱」：   
predict_upcoming.py

因為大熱門（例如潘頓 1.8 倍獨贏，1.1 倍位置）獨贏佔咗成個 Pool 45% 概率，但位置歸一化到 100% 時佢只佔 ~25% 概率；結果所有中冷門（10~20倍馬）嘅 p_prob / w_prob 天然都會大過 1.45！這會導致幾乎每場嘅中冷門都觸發 🎯 大戶位置鎖定。   
predict_upcoming.py

修正方法：用 Harville Formula 由獨贏概率推算「理論位置概率 
P 
place_theo
​
 」，再同實際位置賠率隱含概率（乘返 300%）對比。當 實際位置熱度 / Harville理論位置熱度 >= 1.25 時，先至係真正嘅「大戶買位置唔買W」！

3. 跑法分類 (evaluate_pace_and_style) 只睇上仗 1 場太單薄
目前判斷一隻馬係 LEADER 定 CLOSER 只睇上仗 running_position 嘅第一個數字。   
predict_upcoming.py

問題：如果一隻放頭馬（例如慣常放頭）上仗因為排 12 檔或者出閘慢咗一步留咗喺第 8 位，今仗排 1 檔，你個模型就會將佢當成 CLOSER（後追馬），仲要誤判今場無放頭馬（is_solo_lead）！

優化：既然你喺第 656 行已經查咗 limit(len(horse_codes_list) * 6)（近 6 仗賽果），強烈建議取 近 3 至 5 仗嘅早段走位中位數（或取最快嗰兩仗嘅早段走位） 嚟定義佢嘅固有跑法（Running Style），準確度會大幅提升。   
predict_upcoming.py

4. 保密與安全性提醒 (Security)
你喺 predict_upcoming.py 同 backtest_current_model.py 第 19-20 行，將 Supabase 嘅 service_role secret key 直接寫死咗做 os.environ.get 嘅 default value。如果呢個 Repo 係 Public（或者擺喺 GitHub Actions），任何人都可以用呢條 service_role key 繞過 RLS 刪走或改寫你成個 Database。記得去 Supabase Dashboard Rotate（更換）條 Key，並將 Code 入面嘅 Default Value 改為空字串 ""！   
predict_upcoming.py
+ 1

想我幫你修改 backtest_current_model.py 修復未來數據洩漏並對齊實戰特徵嗎？

是
請幫我修改你上述所建議的...並將整個檔案比我replace去github, 謝謝, 因我不太會修改

已經幫你完成修改並準備好兩個完整嘅 Python 檔案，你可以直接下載並覆蓋（Replace）去你嘅 GitHub Repo：

Your PY files are ready

「PY」圖示
predict_upcoming
 PY 
「PY」圖示
backtest_current_model
 PY 
本次為你完成嘅核心修改清單
1. predict_upcoming.py（實戰預測引擎優化）
Harville (1973) 理論位置概率與「假冷真熱」修正：新增 compute_harville_place_probs() 函數，由獨贏市場勝率嚴格推算每匹馬嘅理論入三甲概率（總和 300% 或少於 7 匹馬時 200%），並將實際位置賠率隱含熱度與 Harville 理論值對比（實際位置熱度 / Harville理論位置熱度 >= 1.25）。徹底解決舊版將位置賠率除 100% 導致幾乎所有中冷門都誤觸發「🎯 大戶位置鎖定」嘅問題。   
predict_upcoming.py
+ 1

近 4 仗綜合跑法識別 (evaluate_pace_and_style)：將原本只睇上仗 1 場 running_position，升級為同時讀取該馬近 4 仗嘅早段走位（結合最快早段走位與中位數），避免放頭馬因為上仗偶發排大外檔留後而被誤判為後追馬 (CLOSER)。   
predict_upcoming.py
+ 1

對數線性概率池化 (Log-Linear Opinion Pooling)：將原本 0.80 * raw_probs + 0.20 * mkt_probs 嘅線性相加，升級為對數幾何加權 
P∝P 
model
0.80
​
 ⋅P 
mkt
0.20
​
 。避免大冷門因為線性平均而被拉高勝率，減少假正期望值（False Positive EV）。   
predict_upcoming.py

移除 Hardcoded Secret Key：已清空程式碼第 23 行寫死嘅 SUPABASE_KEY 預設值，改為純粹由環境變數 os.environ.get("SUPABASE_KEY", "") 讀取，保障資料庫安全。   
predict_upcoming.py

2. backtest_current_model.py（回測引擎修復與對齊）
徹底消除未來數據洩漏 (Zero Data Leakage)：建立咗 prior_runs_lookup[(race_id, horse_code)] 時間序列索引，確保回測每一場比賽時，100% 只讀取該場比賽之前嘅歷史往績嚟推算跑法 (LEADER / CLOSER) 同上仗末段爆發力 (late_burst)，徹底杜絕舊版直接讀取當場賽果 running_position 預知未來嘅 Bug。   
backtest_current_model.py

100% 對齊實戰六大維度與權重：將回測引擎嘅特徵同權重（0.25, 0.20, 0.20, 0.15, 0.10, 0.10）完全對齊 predict_upcoming.py，並真正將上仗競賽事件受阻 (incident_report)、上仗末段 400m 秒數 (last_400m_sec)、升降班、負磅增減、泥地適性、跑道移欄耦合以及外檔被頂三疊懲罰全部納入回測計分。   
predict_upcoming.py
+ 1

保留真實分段時間：喺補齊資料庫 last_400m_sec 時，如果資料庫已經有真實嘅末段 400m 秒數會優先保留，只有喺 NULL 時先用公式估算。   
backtest_current_model.py

放上 GitHub 前嘅 1 個小檢查
因為為咗安全起見，我幫你將 Code 入面寫死咗嗰條 SUPABASE_KEY 清空咗（改為 os.environ.get("SUPABASE_KEY", "")）：   
predict_upcoming.py
+ 1

請確認你 GitHub Repo 嘅 Settings ➔ Secrets and variables ➔ Actions 入面已經設定好 SUPABASE_URL 同 SUPABASE_KEY（你原本嘅 .github/workflows/*.yml 應該已經有設，如果有就完全唔使郁，直接 Replace 兩個 .py 檔就得）。   
hk-racing-main (1).zip

如果你喺自己電腦 Local 行呢兩個 Script，記得先喺 Terminal 設好環境變數（或者放喺 .env）先好執行。





Gemini 是 AI，可能會出錯。

已透過程式碼處理及分析
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
# 🎯 依據量化金融與賽馬學術文獻 (Lessmann et al. 2010, Borowski et al. 2021, Matej et al. 2021)
#    1. 核心原則：預測真實勝出概率 (Win Probability, 加總=100%)，而非離散排名。
#    2. 第一層市場特徵：獨贏 (WIN) 與位置 (PLA) 雙賠率，消除馬會 17.5% 抽水率 (Takeout)。
#    3. 第二至六維實力面 (80% 權重)：往績班次(25%)、檔位勝率(20%)、騎練負磅(20%)、晨操試閘(15%)、配備(10%)、體重(10%)。
#    4. 理性市場融合 (20% 權重)：70% 獨贏隱含勝率 + 30% 位置隱含入位率。
#    5. 資金流模組：Odds Velocity (賠率急跌落飛)、WP 背離度 (大戶位置伏兵)、三方共振 (終極黃金狙擊)。
#    6. 決策規則：計算嚴格期望值 EV = P_model * Odds - 1，捕捉 EV >= +8% 之被低估中等冷馬。
#    7. 資金控管：依據文獻最優 1/4 分數凱利準則 (Fractional Kelly) 計算建議下注比例。
# ==============================================================================

SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://rxmkohhgznfcnhdqegwq.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://racing.on.cc/",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"
}

# 💡 官方網頁字面即時獨贏及位置賠率對照表 (Selenium 與字面精準雙保險)
LITERAL_LIVE_ODDS = {}


# 🌟 頂級騎練長效統計加權庫 (2024-2026 兩季大數據回測認證)
ELITE_JOCKEYS = {
    "潘頓": 1.00, "麥道朗": 0.95, "布文": 0.92, "何澤堯": 0.88, 
    "田泰安": 0.82, "艾兆禮": 0.82, "霍宏聲": 0.78, "巴度": 0.72,
    "蔡明紹": 0.70, "班德禮": 0.70, "梁家俊": 0.68
}

ELITE_TRAINERS = {
    "蔡約翰": 0.92, "方嘉柏": 0.88, "沈集成": 0.88, "呂健威": 0.86,
    "告東尼": 0.84, "廖康銘": 0.84, "伍鵬志": 0.85, "姚本輝": 0.80,
    "文家良": 0.78, "賀賢": 0.76, "羅富全": 0.78
}

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

# 🌟 跑道途程檔位官方統計勝率矩陣 (2024至今沙田 A 跑道)
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

# 🌟 沙田全天候跑道 (泥地) 專用檔位統計矩陣
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

# 🌟 跑馬地 (谷草) 專用檔位統計矩陣 (涵蓋 A/B/C/C+3 賽道極端偏差)
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

# 🌟 精確切分每匹馬之專屬晨操文本
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

# 🌟 晨操與試閘動態狀態解析器 (只取【本仗賽前最近一課】，上次賽事前的全部過濾)
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

    # 兼容馬會 LocalTrackwork 各種表格排版格式，提取所有晨操快跳與拍跳課數
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

    # 若文本中有秒數紀錄但正則未完全匹配日期，按秒數課次保底統計
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

# 🌟 跑法與走位分析器 (結合近3~5仗早段走位統計，精準識別 Leader, Prominent, Midfield, Closer 及末段爆發力)
def evaluate_pace_and_style(h, past_record, past_runs_list=None):
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
            start_positions.append(s_pos)
            # 近兩仗內曾展現留後狂追爆發力
            if idx <= 1 and s_pos >= 8 and (s_pos - f_pos >= 5 or f_pos <= 5):
                late_burst = True

    style = "MIDFIELD"
    if start_positions:
        # 採用近仗早段走位加權（取最快兩仗均值與中位數結合，避免單場排外檔偶發留後誤判）
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

# 🌟 學術級 Harville (1973) 理論位置概率推算器 (由獨贏勝率嚴格推導前三名/前二名入位概率)
def compute_harville_place_probs(win_probs_pct, num_places=3):
    p = np.clip(np.array(win_probs_pct, dtype=float) / 100.0, 1e-5, 0.999)
    p = p / p.sum()
    n = len(p)
    place_probs = np.zeros(n, dtype=float)
    for i in range(n):
        p1 = p[i]
        # 第 2 名概率
        p2 = 0.0
        for j in range(n):
            if j == i:
                continue
            denom1 = max(1e-6, 1.0 - p[j])
            p2 += p[j] * (p[i] / denom1)
        # 第 3 名概率
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

# 🌟 配備變更分析器
def score_gear(gear_str):
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

# 🌟 排位體重增減分析器
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

# 🌟 往績近況與上仗賽事對比分析器
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

                # 🌟 專業評馬人暗湧雷達：競賽事件報告 (incident_report) 塞車受阻/外疊蝕位黑馬
                inc_rep = str(last.get("incident_report") or "")
                trouble_kws = ["受困而未能望空", "未能望空", "勒避", "走外疊", "沒有遮擋", "無遮擋", "出閘笨拙", "碰撞", "失去平衡", "嚴重受阻", "受擠迫", "受阻礙", "難以望空", "被帶出外疊", "被迫走外疊"]
                if any(kw in inc_rep for kw in trouble_kws):
                    try: p_num = int(last.get("place_num") or 99)
                    except: p_num = 99
                    if p_num <= 8:
                        score += 0.18
                        tags.append("🚧 上仗受阻/走外疊蝕位 (隱形實力馬)")

                # 🌟 末段 400 米極速秒數 (last_400m_sec) 評估
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

                # 🌟 賽事相隔天數 (Rest Days) 體能新鮮感 (自動由兩場賽事日期相減補齊)
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
                    if curr_j in ["潘頓", "艾兆禮", "何澤堯", "布文", "巴度", "麥道朗"]:
                        score += 0.15
                        tags.append(f"⭐ 換強配大師傅 ({curr_j})")
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
    """
    香港賽馬會彩池開盤守門員：
    馬會固定於「賽事前一日中午 12:00 (HKT)」正式開售彩池並提供賠率。
    若當前香港時間早於賽前一日 11:30 AM，代表尚未開盤，嚴禁抓取東網舊盤或啟動 Chrome！
    """
    try:
        from datetime import datetime, timedelta, timezone
        hkt = timezone(timedelta(hours=8))
        now_hkt = datetime.now(hkt)
        race_dt = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        # 開盤時間：賽事前一日 11:30 HKT
        open_dt = datetime(race_dt.year, race_dt.month, race_dt.day, 11, 30, tzinfo=hkt) - timedelta(days=1)
        # 若賽事已經過期超過 1 天，也無需再抓即時賠率
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

    # 🌟 1. 通道 1: 馬會官方 getJSON 實時數據流
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

    # 🌟 2. 通道 2: 東網 (on.cc) 賽日即時鏡像 (嚴格核對賽日日期，絕不誤用上期舊賠率)
    url_oncc = f"https://racing.on.cc/racing/rat/current/rjratb{race_no:04d}x0.html"
    try:
        r = requests.get(url_oncc, headers=HEADERS, timeout=6)
        if r.status_code == 200 and r.text:
            r.encoding = "big5"
            soup = BeautifulSoup(r.text, "html.parser")
            page_txt = soup.get_text()
            # 驗證東網頁面日期是否與 target_date (YYYY-MM-DD) 吻合
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

    # 🌟 3. 通道 3: 嘗試 Selenium 動態渲染
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
    """
    📡 同日實時賽道偏差自適應偵測器 (Intraday Live Track Bias Detector)
    自動掃描當日已跑完場次 (第 1 場起) 之官方賽果，統計頭三名上名馬之：
    1. 檔位分佈 (1-6 內中檔 vs 8+ 外檔)
    2. 沿途走位跑法 (早段守前 4 位前置馬 vs 第 8 位或以後留後馬)
    從而動態微調同日其後場次之檔位與步速權重。
    """
    inside_top3 = 0
    wide_top3 = 0
    front_top3 = 0
    closer_top3 = 0
    total_top3 = 0
    completed_races = 0

    for r_no in range(1, 11):
        url = f"https://racing.hkjc.com/racing/information/Chinese/Racing/LocalResults.aspx?RaceDate={date_hkjc}&Racecourse={venue}&RaceNo={r_no}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=6)
            if resp.status_code != 200 or "沒有相關資料" in resp.text:
                break
            soup = BeautifulSoup(resp.text, "html.parser")
            perf_tb = None
            for tb in soup.find_all("table"):
                txt_h = tb.get_text()
                if "名次" in txt_h and "馬名" in txt_h and "檔位" in txt_h:
                    perf_tb = tb
                    break
            if not perf_tb:
                break

            headers = [th.get_text(strip=True) for th in perf_tb.find("tr").find_all(["td", "th"])]
            draw_idx = next((i for i, h in enumerate(headers) if "檔位" in h), 7)
            pos_idx = next((i for i, h in enumerate(headers) if "沿途走位" in h), 9)

            race_has_top3 = False
            for tr in perf_tb.find_all("tr")[1:]:
                tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
                if len(tds) <= max(draw_idx, 2):
                    continue
                pl_str = tds[0].strip()
                if pl_str in ("1", "2", "3", "01", "02", "03"):
                    race_has_top3 = True
                    total_top3 += 1
                    d_str = tds[draw_idx].strip()
                    if d_str.isdigit():
                        d_val = int(d_str)
                        if d_val <= 6:
                            inside_top3 += 1
                        elif d_val >= 8:
                            wide_top3 += 1
                    if len(tds) > pos_idx:
                        pos_parts = [int(x) for x in tds[pos_idx].split() if x.isdigit()]
                        if pos_parts:
                            if pos_parts[0] <= 4:
                                front_top3 += 1
                            elif pos_parts[0] >= 8:
                                closer_top3 += 1
            if race_has_top3:
                completed_races += 1
            else:
                break
        except Exception:
            break

    bias = {
        "completed_races": completed_races,
        "inside_boost": 0.0,
        "wide_boost": 0.0,
        "front_boost": 0.0,
        "closer_boost": 0.0,
        "summary": "尚未有已完成賽事"
    }
    if completed_races >= 1 and total_top3 >= 3:
        inside_ratio = inside_top3 / total_top3
        wide_ratio = wide_top3 / total_top3
        front_ratio = front_top3 / total_top3
        closer_ratio = closer_top3 / total_top3

        notes = []
        if inside_ratio >= 0.60:
            bias["inside_boost"] = 0.08
            notes.append(f"內中檔(1-6檔)上名率 {int(inside_ratio*100)}% 佔優")
        elif wide_ratio >= 0.55:
            bias["wide_boost"] = 0.07
            notes.append(f"中外檔(8檔+)上名率 {int(wide_ratio*100)}% 佔優")

        if front_ratio >= 0.55:
            bias["front_boost"] = 0.08
            notes.append(f"前置守好位(頭段前4)上名率 {int(front_ratio*100)}% 佔優")
        elif closer_ratio >= 0.50:
            bias["closer_boost"] = 0.08
            notes.append(f"後追衝刺(頭段8位後)上名率 {int(closer_ratio*100)}% 佔優")

        bias["summary"] = f"已跑 {completed_races} 場: " + (" | ".join(notes) if notes else "偏差均衡")
    return bias

def run_upcoming():
    print("=== 🏇 香港賽馬 AI：學術級「勝率量化預測 + 正期望值 (EV) + 1/4 Kelly 資金控管」全息引擎 ===")
    target_date, date_hkjc, venue = detect_upcoming_meeting()
    intraday_bias = detect_intraday_track_bias(date_hkjc, venue)
    print(f"賽事日期: {target_date} ({venue}) | 實時賽道偏差偵測: {intraday_bias['summary']}")

    total_races = 0
    sb_master = create_client(SUPABASE_URL, SUPABASE_KEY)

    # 預先載入歷史 races 場次資訊映射 (distance, race_class, track_type, venue)
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

        # 標準化 odds_map: 確保每個條目均為字典結構 {'win': float, 'pla': float or None}
        norm_odds_map = {}
        for k, v in (odds_map or {}).items():
            if isinstance(v, dict):
                norm_odds_map[k] = v
            elif isinstance(v, (int, float)):
                norm_odds_map[k] = {"win": float(v), "pla": None}
        odds_map = norm_odds_map

        # 讀取 Supabase 上一輪儲存的預測紀錄 (用於計算 Odds Velocity 賠率動量、落飛斜率與智慧標籤延續)
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
        except Exception as e:
            pass

        # 🛡️ 防倒退保護機制：僅在「已開盤但馬會夜間臨時維護」時繼承賠率；若賽事根本尚未開盤，強制保持空賠率以清除誤抓舊數據
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

        # 🌟 全場步速與跑法形勢預先推演 (Race-level Pace Mapping)
        horse_styles = {}
        horse_late_bursts = {}
        horse_past_cache = {}
        # 批量一次查詢本場所有馬匹的歷史賽果 (避免 N+1 查詢，保存近 5 仗紀錄以精準計算跑法與上仗數據)
        horse_codes_list = [h["horse_code"] for h in horses if h.get("horse_code")]
        batch_past_map = {}
        batch_history_map = {}
        try:
            if horse_codes_list and sb_master:
                r_batch = sb_master.table("race_results").select("*").in_("horse_code", horse_codes_list).order("race_id", desc=True).limit(len(horse_codes_list) * 6).execute()
                for row in (r_batch.data or []):
                    hc = row.get("horse_code")
                    rid = str(row.get("race_id") or "")
                    # 只取早於當前賽事日期的歷史紀錄
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
            st, lb = evaluate_pace_and_style(h, past, past_list)
            horse_styles[h_no] = st
            horse_late_bursts[h_no] = lb

        num_leaders = sum(1 for s in horse_styles.values() if s == "LEADER")
        num_prominent = sum(1 for s in horse_styles.values() if s == "PROMINENT")
        is_solo_lead = (num_leaders <= 1)
        is_fast_pace = (num_leaders >= 3 or (num_leaders == 2 and num_prominent >= 4))

        # 🌟 實時市場資訊與 Harville 位置理論概率預先掃描 (Market Probability & Harville Divergence Pre-scan)
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
                # 位置彩池總概率為 num_places * 100% (例如 3 個位置加總為 300%)
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
        tags_meta = {}
        for h in horses:
            h_no = h["horse_no"]
            h_tags = []

            # 🌟 維度 1: 往績近況、途程對應與班次級數 (25% 權重)
            s_form_trans, f_tags, last_race_date, calc_rest_days = evaluate_form_and_transition(
                h["horse_code"], meta, h, h["form"], avg_r, sb_master,
                past_record=horse_past_cache.get(h_no), target_date=target_date
            )
            h["rest_days"] = calc_rest_days
            r_scale = (float(h["rating"]) - avg_r) / 7.0
            pillar_1 = r_scale * 0.55 + s_form_trans * 0.45
            h_tags.extend(f_tags)

            # 🌟 維度 2: 跑道途程檔位官方統計勝率 (20% 權重)
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

            # 🌟 維度 3: 騎練合作與負磅/減磅 (20% 權重)
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

            # 頂級騎練長效加權與原庄連騎加成
            clean_j = re.sub(r"\s*\(.*?\)", "", h["jockey"]).strip()
            clean_t = h.get("trainer", "").strip()
            j_tier = ELITE_JOCKEYS.get(clean_j, 0.40)
            t_tier = ELITE_TRAINERS.get(clean_t, 0.45)
            jt_tier_score = (j_tier * 0.65 + t_tier * 0.35) - 0.50

            loyalty_bonus = 0.0
            past_rec = horse_past_cache.get(h_no)
            if past_rec and clean_j:
                last_j = re.sub(r"\s*\(.*?\)", "", str(past_rec.get("jockey") or "")).strip()
                if last_j == clean_j and clean_j in ELITE_JOCKEYS:
                    loyalty_bonus = 0.12
                    h_tags.append("🤝 王牌原庄配搭連騎")

            pillar_3 = w_score * 0.40 + (claim_bonus + home_bonus) * 0.25 + (jt_tier_score + loyalty_bonus) * 0.35

            # 🌟 維度 4: 晨操數據與試閘評語 (15% 權重)
            h_tw_text = tw_map.get(h_no, "")
            tw_score, tw_tags, fg_count = evaluate_trackwork(h_tw_text, h["jockey"], target_date, last_race_date)
            h["fast_gallop_count"] = fg_count
            pillar_4 = tw_score
            h_tags.extend(tw_tags)

            # 🌟 維度 5: 配備變更殺機信號 (10% 權重)
            g_score, g_tags = score_gear(h["gear"])
            pillar_5 = g_score
            h_tags.extend(g_tags)

            # 🌟 維度 6: 排位體重增減分析 (10% 權重)
            wt_score, wt_tags = score_body_weight(h["weight_diff"])
            pillar_6 = wt_score
            h_tags.extend(wt_tags)

            # 🌟 全場步速與跑法戰術加成 (深度耦合：賽道 × 跑道移欄 × 途程幾何)
            h_style = horse_styles.get(h_no, "MIDFIELD")
            is_hv = (venue == "HV" or "跑馬地" in str(venue) or "谷" in str(venue))
            is_awt = (meta.get("track_type") == "全天候跑道" or "泥" in str(meta.get("track_type", "")))
            is_st_straight = (not is_hv and not is_awt and dist == 1000)
            is_c_rail = ("C" in str(course).upper() or "B+2" in str(course).upper())
            # 首彎極短途程：谷草1000/1200/1650 及 泥地1650 (出閘不足200米即入急彎)
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
                    # 谷草短直路(310m)後追折扣 vs 田草長直路(430m)後追全面釋放
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

            # 🏟️ 賽道與跑道移欄 × 跑法物理耦合修正
            if is_awt:
                # 沙田泥地：前置馬免食泥頭 (Kickback) 優勢極大，留後馬食泥沙劣勢明顯
                if h_style in ("LEADER", "PROMINENT") and h["draw"] <= 6:
                    pace_bonus += 0.08
                    h_tags.append("🏜️ 泥地前置免食泥頭優勢")
                elif h_style == "CLOSER" and not is_fast_pace:
                    pace_bonus -= 0.06
                    h_tags.append("⚠️ 泥地留後食泥頭劣勢")
            elif is_hv and is_c_rail:
                # 谷草 C / C+3 窄道短直路：內欄領放與守Box位極難被越過
                if h_style in ("LEADER", "PROMINENT") and h["draw"] <= 5:
                    pace_bonus += 0.08
                    h_tags.append(f"🏟️ 谷草{course}欄短直路前置優勢")
                elif h_style == "CLOSER" and not is_fast_pace:
                    pace_bonus -= 0.06
            elif not is_hv and not is_awt and is_c_rail:
                # 沙田草地 C / C+3 窄道：季初/好地處女草皮極利中長途(1600m+)及1200m前置順放與Box位
                if dist >= 1600 and h_style in ("LEADER", "PROMINENT"):
                    pace_bonus += 0.14
                    h_tags.append(f"🏟️ 田草{course}欄中長途前置高速路")
                elif dist == 1200 and h_style in ("LEADER", "PROMINENT") and h["draw"] <= 6:
                    pace_bonus += 0.08
                    h_tags.append(f"🏟️ 田草{course}欄短途內中檔先手")
                elif dist >= 1600 and h_style == "CLOSER" and not is_fast_pace:
                    pace_bonus -= 0.08

            # 🎯 C+3 / C 欄同軌窄道往績專家偵測 (如上仗於C/C+3欄入前三名，再跑C/C+3欄額外加分)
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

            # 🌟 外檔跟前被頂三疊無遮擋風險偵測 (沙田1000米直路豁免；首彎極短途程懲罰加倍)
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

            # 🌟 上仗末段爆發力暗湧加成 (分段尾速狂追)
            if horse_late_bursts.get(h_no):
                pillar_1 += 0.15
                h_tags.append("🚀 上仗末段狂追 (暗湧實力馬)")

            # 🌟 資金流模組 1: 賠率動量與急跌落飛偵測 (Odds Velocity)
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
                    if odds_drop_pct >= 20.0:  # 賠率急跌 20% 以上 (大戶掃貨)
                        odds_velocity_bonus = +0.15
                        h_tags.append(f"🔥 聰明錢落飛 (-{int(odds_drop_pct)}%)")
                    elif prev_had_inflow and win_odd <= prev_win * 1.05:
                        # 保持在落飛低位 (跨輪次延續保護)
                        odds_velocity_bonus = +0.15
                        existing_drop_tag = next((t for t in prev_tags if "聰明錢落飛" in str(t)), "🔥 聰明錢落飛")
                        h_tags.append(existing_drop_tag)
                    elif odds_drop_pct <= -25.0: # 顯著回飛 25% 以上 (大戶棄選)
                        odds_velocity_bonus = -0.10
                        h_tags.append(f"❄️ 資金冷卻回飛 (+{int(abs(odds_drop_pct))}%)")
            elif prev_had_inflow:
                odds_velocity_bonus = +0.15
                existing_drop_tag = next((t for t in prev_tags if "聰明錢落飛" in str(t)), "🔥 聰明錢落飛")
                h_tags.append(existing_drop_tag)

            # 🌟 資金流模組 2: Harville WP 賠率背離度偵測 (實際位置隱含熱度顯著超越 Harville 理論位置概率 —— 真正捕捉大戶位置伏兵)
            wp_divergence_bonus = 0.0
            p_prob_actual = pla_mkt_probs_map.get(h_no, 0.0)
            p_prob_harville = harville_pla_probs_map.get(h_no, 0.0)
            if win_odd and win_odd >= 7.0 and pla_odd and pla_odd > 1.0 and p_prob_harville > 0.0:
                wp_ratio = p_prob_actual / p_prob_harville
                if wp_ratio >= 1.25:  # 實際位置資金佔比超越 Harville 理論值 25% 以上
                    wp_divergence_bonus = +0.12
                    h_tags.append("🎯 大戶位置鎖定 (假冷真熱)")

            # 📡 模組 3: 同日已跑場次實時跑道偏差動態微調 (Intraday Live Track Bias)
            intraday_bonus = 0.0
            if intraday_bias.get("completed_races", 0) >= 1 and not is_st_straight:
                if intraday_bias["inside_boost"] > 0 and h["draw"] <= 6:
                    intraday_bonus += intraday_bias["inside_boost"]
                    h_tags.append("📡 今日實時偏差: 1-6內中檔利位")
                elif intraday_bias["wide_boost"] > 0 and h["draw"] >= 8:
                    intraday_bonus += intraday_bias["wide_boost"]
                    h_tags.append("📡 今日實時偏差: 外疊場地有利")

                if intraday_bias["front_boost"] > 0 and h_style in ("LEADER", "PROMINENT"):
                    intraday_bonus += intraday_bias["front_boost"]
                    h_tags.append("📡 今日實時偏差: 場地乾快利前置")
                elif intraday_bias["closer_boost"] > 0 and h_style == "CLOSER":
                    intraday_bonus += intraday_bias["closer_boost"]
                    h_tags.append("📡 今日實時偏差: 直路利後追衝刺")

            # 綜合六大專業維度 + 步速戰術加成 + 資金流雙維加成 + 同日實時偏差微調
            total_feature = (
                (pillar_1 * 0.25) + 
                (pillar_2 * 0.20) + 
                (pillar_3 * 0.20) + 
                (pillar_4 * 0.15) + 
                (pillar_5 * 0.10) + 
                (pillar_6 * 0.10)
            ) + pace_bonus + odds_velocity_bonus + wp_divergence_bonus + intraday_bonus
            scores.append(total_feature)
            tags_meta[h_no] = list(set(h_tags))

        # 🌟 學術核心 1：Race-level 競爭概率模型 (Softmax 歸一化勝率加總 = 100%)
        scores = np.array(scores)
        exp_s = np.exp(scores * 2.2)
        raw_probs = (exp_s / exp_s.sum()) * 100.0

        # 🌟 學術核心 2：對數線性概率池化校準 (Log-Linear Opinion Pooling，避免線性相加高估大冷門假正期望值)
        if has_win_odds:
            safe_raw = np.clip(raw_probs, 1e-3, 100.0)
            safe_mkt = np.clip(mkt_composite_probs, 1e-3, 100.0)
            log_pooled = (safe_raw ** 0.80) * (safe_mkt ** 0.20)
            final_probs = (log_pooled / log_pooled.sum()) * 100.0
        else:
            final_probs = raw_probs

        scored = []
        for i, h in enumerate(horses):
            h_no = h["horse_no"]
            odds_info = odds_map.get(h_no, {})
            win_odd = odds_info.get("win")
            pla_odd = odds_info.get("pla")

            tags = tags_meta.get(h_no, [])
            jockey_trainer_str = f"{h['jockey']} / {h['trainer']}" if h.get("trainer") else h["jockey"]

            p_model = final_probs[i] / 100.0
            is_val = False
            ev_pct = 0.0
            kelly_pct = 0.0

            # 🌟 學術核心 3 & 4：嚴格期望值 (EV) 計算與 1/4 Kelly 資金控管
            if win_odd and win_odd > 1.0:
                # 數學期望值公式: EV = P_model * Odds - 1
                ev = (p_model * win_odd) - 1.0
                ev_pct = round(ev * 100.0, 1)

                # 關注中等冷門 (5.0 - 25.0倍) 之正期望值機會；對於超過25倍極冷門要求基本實力門檻 (P >= 4%)
                if ev >= 0.08: # 期望值超越 +8% 門檻 (足以覆蓋模型抽樣誤差與交易滑點)
                    if win_odd <= 25.0 or p_model >= 0.04:
                        is_val = True
                        b = win_odd - 1.0
                        # 1/4 分數凱利公式 (Fractional Kelly)
                        kelly_1_4 = (ev / (4.0 * b)) * 100.0
                        kelly_pct = round(min(5.0, max(0.5, kelly_1_4)), 1) # 單注風險控制在 0.5% ~ 5.0% 本金

                        tags.append(f"💎 正期望值 EV: +{ev_pct}%")
                        tags.append(f"📊 建議注碼: 1/4 Kelly ({kelly_pct}%)")

            if pla_odd:
                tags.append(f"位置賠率: {pla_odd}倍")

            # 🛡️ 嚴格防溢位保護：PostgreSQL NUMERIC(5, 2) 欄位絕對值必須嚴格 < 10^3 (即 -999.99 ~ 999.99)
            safe_ev_pct = round(max(-99.0, min(999.0, ev_pct)), 1)
            safe_win_odd = round(min(999.0, max(1.0, win_odd)), 1) if win_odd and win_odd > 1.0 else None
            safe_win_prob = round(float(min(100.0, max(0.0, final_probs[i]))), 2)

            # 若馬會尚未開盤 (safe_win_odd 為 None)，自動根據模型勝率生成「AI 公允參考賠率 (含17.5%抽水校準)」填入 market_odds，確保資料庫欄位不為 NULL
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
            is_high_ev = (item["combo_synergy"] >= 8.0) # EV >= +8%

            # 🌟 資金流模組 3: 三方共振 (技術面前4 + 正期望值 + 聰明錢確認)
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
predict_upcoming.py
正在顯示「predict_upcoming.py」。
