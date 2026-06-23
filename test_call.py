import urllib.request
import json
import time

url = "http://127.0.0.1:8000/scrape/sync"
payload = {
    "keyword": "restaurant",
    "location": "Indore",
    "limit": 3,
    "find_emails": False
}

req = urllib.request.Request(
    url,
    data=json.dumps(payload).encode('utf-8'),
    headers={'Content-Type': 'application/json'},
    method='POST'
)

print("Sending request to scraper...")
start = time.time()
try:
    with urllib.request.urlopen(req) as response:
        elapsed = time.time() - start
        print(f"Status Code: {response.status}")
        print(f"Elapsed Time: {elapsed:.2f} seconds")
        data = json.loads(response.read().decode('utf-8'))
        print("Response JSON:")
        print(json.dumps(data, indent=2))
except Exception as e:
    print(f"Failed to perform request: {e}")
