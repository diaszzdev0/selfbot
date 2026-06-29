import requests

TOKEN = '7a9131befcdc938552460cab541b28d56fcfbb2a-916e86b458deb44fc8c869c10c98863a82dab435eb5097a6e504ec5ee372de8d'
APP_ID = 'd676168381434cdfb3126b40296de390'

r = requests.get(f'https://api.squarecloud.app/v2/apps/{APP_ID}', headers={'Authorization': TOKEN})
with open('sq_result.txt', 'w', encoding='utf-8') as f:
    f.write(f'{r.status_code}\n{r.text}')
