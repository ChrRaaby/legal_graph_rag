import json, os, urllib.request, urllib.error
from dotenv import load_dotenv
load_dotenv(".env")
KEY = os.environ["GOOGLE_API_KEY"]
for mid in ("gemini-3.5-flash", "gemini-3.5-flash-lite"):
    req = urllib.request.Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{mid}:generateContent?key={KEY}",
        data=json.dumps({"contents": [{"parts": [{"text": "hi"}]}],
                         "generationConfig": {"maxOutputTokens": 1}}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=30)
        print(f"{mid:26} OK")
    except urllib.error.HTTPError as e:
        body = e.read(1400).decode("utf-8", "replace")
        print(f"{mid:26} HTTP {e.code}")
        try:
            d = json.loads(body)
            err = d.get("error", {})
            print(f"   status={err.get('status')}  msg={err.get('message','')[:150]}")
            for det in err.get("details", []):
                if "quotaMetric" in json.dumps(det) or det.get("@type","").endswith("QuotaFailure"):
                    for v in det.get("violations", []):
                        print(f"   quota: metric={v.get('quotaMetric','?').split('/')[-1]} "
                              f"id={v.get('quotaId','?')} value={v.get('quotaValue','?')}")
                if det.get("@type", "").endswith("RetryInfo"):
                    print(f"   retry after: {det.get('retryDelay')}")
        except Exception:
            print("   " + body[:200])
