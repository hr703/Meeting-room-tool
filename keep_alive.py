import urllib.request
try:
    urllib.request.urlopen('https://meeting-room-new.onrender.com', timeout=10)
    print('OK')
except Exception as e:
    print(f'Error: {e}')
