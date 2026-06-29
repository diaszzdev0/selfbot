#!/usr/bin/env python3
"""Test SquareCloud API"""

import requests

TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-a53eac121622dc57f38f43db364273eb6f54e007621728393760a142be11f75e'
APP_ID = 'd676168381434cdfb3126b40296de390'

# Test different endpoints
endpoints = [
    f'https://api.squarecloud.app/v1/apps',
    f'https://api.squarecloud.app/v1/apps/{APP_ID}',
    f'https://api.squarecloud.app/v2/apps/{APP_ID}',
    f'https://api.squarecloud.app/v1/apps/{APP_ID}/status',
    f'https://api.squarecloud.app/v2/apps/{APP_ID}/deploy',
]

for url in endpoints:
    try:
        r = requests.get(url, headers={'Authorization': TOKEN}, timeout=10)
        print(f"GET {url}")
        print(f"  Status: {r.status_code}")
        print(f"  Response: {r.text[:300]}")
        print()
    except Exception as e:
        print(f"GET {url} -> Erro: {e}")
        print()

# Test POST for redeploy
print("Testing POST endpoints...")
post_endpoints = [
    f'https://api.squarecloud.app/v1/apps/{APP_ID}/redeploy',
    f'https://api.squarecloud.app/v2/apps/{APP_ID}/redeploy',
    f'https://api.squarecloud.app/v1/apps/{APP_ID}/deploy',
]

for url in post_endpoints:
    try:
        r = requests.post(url, headers={'Authorization': TOKEN}, timeout=30)
        print(f"POST {url}")
        print(f"  Status: {r.status_code}")
        print(f"  Response: {r.text[:300]}")
        print()
    except Exception as e:
        print(f"POST {url} -> Erro: {e}")
        print()
