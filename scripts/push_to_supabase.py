import os
import sys
from datetime import datetime
from supabase import create_client, Client

# GitHub Secrets에서 가져온 환경변수
URL = os.environ.get("SUPABASE_URL")
KEY = os.environ.get("SUPABASE_SERVICE_KEY")

def push_daily_report(results):
    if not URL or not KEY:
        print("Error: Missing Supabase Credentials")
        sys.exit(1)

    supabase: Client = create_client(URL, KEY)
    today = datetime.now().strftime('%Y-%m-%d')

    for model, pnl, profit, logic in results:
        data = {
            "date": today,
            "model_id": model,
            "logic": logic,
            "pnl_percent": pnl,
            "net_profit_krw": profit,
            "status": "Win" if pnl >= 0 else "Loss"
        }
        supabase.table("trading_logs").insert(data).execute()
        print(f"Synced {model}")

if __name__ == "__main__":
    # 매매 봇 실행 후 얻은 결과 예시 (리스트 형태)
    sample_results = [
        ("Model A", 1.42, 42600, "Trend-Following"),
        ("Model B", 0.85, 15400, "Mean-Reversion")
    ]
    push_daily_report(sample_results)
