import urllib.request
import json
url = "http://localhost:8000/api/reports/stock-balance"
try:
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req) as response:
        data = json.loads(response.read().decode())
        if 'data' in data:
            data = data['data']
        for item in data:
            if 'bread' in item.get('item_name', '').lower():
                print(f"Item: {item['item_name']}")
                print(f"Bought (Purchased): {item['purchased_qty']}")
                print(f"Pushed (Issued to kitchen): {item['pushed_qty']}")
                print("-" * 20)
except Exception as e:
    print("Failed to fetch:", e)
