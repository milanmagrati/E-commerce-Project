import requests

url = 'https://api.ocr.space/parse/image'
payload = {
    'apikey': 'K86992727988957',
    'language': 'eng',
    'isTable': True,
}

# Create a small dummy png or just download one
import urllib.request
urllib.request.urlretrieve("https://upload.wikimedia.org/wikipedia/commons/1/12/Test_image.jpg", "test.jpg")

with open("test.jpg", "rb") as f:
    file_bytes = f.read()

files = {'file': ("test.jpg", file_bytes)}
response = requests.post(url, data=payload, files=files)
print(response.status_code)
print(response.json())
