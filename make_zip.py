import zipfile, os

files = [
    'app.py','bot_logic.py','bot.py','imap_optimizer.py',
    'models.py','requirements.txt','squarecloud.app',
    'ocr_comprovante.py','token_validator.py','.env'
]

out = 'deploy_final.zip'
if os.path.exists(out):
    os.remove(out)

with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for f in files:
        if os.path.exists(f):
            z.write(f)
            print('+', f)
        else:
            print('MISSING:', f)
    for root, dirs, fs in os.walk('templates'):
        for fname in fs:
            path = os.path.join(root, fname)
            z.write(path)
            print('+', path)

print('TOTAL:', os.path.getsize(out), 'bytes')
