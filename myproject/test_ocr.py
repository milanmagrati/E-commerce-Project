import requests

url = 'https://api.ocr.space/parse/image'
payload = {
    'apikey': 'K86992727988957',
    'language': 'eng',
    'isTable': True,
}
# We don't have a real image, we will use a small dummy text image or URL
payload['url'] = 'https://upload.wikimedia.org/wikipedia/commons/1/12/Test_image.jpg'

response = requests.post(url, data=payload)
print(response.status_code)
print(response.json())
