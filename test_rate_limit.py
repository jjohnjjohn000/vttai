import openai
import httpx
try:
    resp = httpx.Response(429, request=httpx.Request("POST", "http://test"))
    err = openai.RateLimitError(message="Quota_429_Cached: Skipping exhausted key", response=resp, body={})
    print("Success:", repr(err))
except Exception as e:
    print("Error:", repr(e))
