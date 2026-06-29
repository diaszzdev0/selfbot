import os, py_compile, traceback, sys
root = r'd:/SELFBOT M'
errors = []
for dirpath, _, filenames in os.walk(root):
    for f in filenames:
        if f.endswith('.py'):
            path = os.path.join(dirpath, f)
            try:
                py_compile.compile(path, doraise=True)
            except Exception:
                errors.append((path, traceback.format_exc()))

if errors:
    print('TOTAL ERRORS:', len(errors))
    for p, e in errors:
        print('---', p)
        print(e)
else:
    print('No syntax errors found')
