#!/usr/bin/env python3
"""Test SquareCloud API v2 endpoints"""

import requests

TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-a53eac121622dc57f38f43db364273eb6f54e007621728393760a142be11f75e'
APP_ID = 'd676168381434cdfb3126b40296de390'

# Test more v2 endpoints
endpoints = [
    ('GET', f'https://api.squarecloud.app/v2/apps/{APP_ID}'),
    ('GET', f'https://api.squarecloud.app/v2/apps/{APP_ID}/logs'),
    ('POST', f'https://api.squarecloud.app/v2/apps/{APP_ID}/restart'),
    ('POST', f'https://api.squarecloud.app/v2/apps/{APP_ID}/start'),
    ('POST', f'https://api.squarecloud.app/v2/apps/{APP_ID}/stop'),
    ('PATCH', f'https://api.squarecloud.app/v2/apps/{APP_ID}'),
    ('PUT', f'https://api.squarecloud.app/v2/apps/{APP_ID}'),
]

for method, url in endpoints:
    try:
        if method == 'GET':
            r = requests.get(url, headers={'Authorization': TOKEN}, timeout=10)
        else:
            r = requests.request(method, url, headers={'Authorization': TOKEN}, timeout=30)
        print(f"{method} {url}")
        print(f"  Status: {r.status_code}")
        print(f"  Response: {r.text[:400]}")
        print()
    except Exception as e:
        print(f"{method} {url} -> Erro: {e}")
        print()

# Try with form data
print("Trying POST with form data...")
url = f'https://api.squarecloud.app/v2/apps/{APP_ID}'
try:
    r = requests.post(url, headers={'Authorization': TOKEN}, data={}, timeout=30)
    print(f"POST {url}")
    print(f"  Status: {r.status_code}")
    print(f"  Response: {r.text[:400]}")
except Exception as e:
    print(f"Erro: {e}")
