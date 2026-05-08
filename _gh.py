import requests

# Lista repos do usuário
r = requests.get('https://api.github.com/users/diaszzdev0/repos', timeout=15)
for repo in r.json():
    print(repo['name'], '|', repo['clone_url'], '|', repo['updated_at'])
