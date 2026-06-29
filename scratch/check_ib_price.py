import urllib.request
import re

url = "https://www.industrybuying.com/wood-working-lathe-lion-MAC.WOO.77700458"
req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
try:
    html = urllib.request.urlopen(req).read().decode('utf-8')
    matches = re.finditer(r'<([a-zA-Z0-9]+)[^>]*>[^<]*2,?831[^<]*</\1>', html)
    for i, m in enumerate(matches):
        if i > 5: break
        idx = m.start()
        print(f"HTML snippet {i}: {html[idx-100:idx+100]}")
except Exception as e:
    print("Error:", e)
