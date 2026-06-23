import urllib.request
import urllib.error
import json

url = 'http://127.0.0.1:8000/api/orders/follow-ups/add/'
data = json.dumps({
    'name': 'test',
    'phone': '1234567890',
    'lead_source': '',
    'product_ids': [],
    'followup_1': '',
    'followup_2': '',
    'status': '',
    'remarks': ''
}).encode('utf-8')

headers = {
    'Content-Type': 'application/json'
}

req = urllib.request.Request(url, data=data, headers=headers)

try:
    with urllib.request.urlopen(req) as response:
        print(response.read().decode('utf-8'))
except urllib.error.HTTPError as e:
    print(f"HTTP Error: {e.code} {e.reason}")
    print(e.read().decode('utf-8')[:1000])
except Exception as e:
    print(f"Error: {e}")
