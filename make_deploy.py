import zipfile, os, fnmatch

ignore_patterns = ["*.db", "instance/", "logs/", "__pycache__/", "*.pyc", "imap_cache/", "*.zip", ".env", "venv/", ".git/", "uids_usados.txt", "*.lock"]

def should_ignore(relpath):
    relpath = relpath.replace("\\", "/")
    name = relpath.split("/")[-1]
    for p in ignore_patterns:
        if fnmatch.fnmatch(name, p) or fnmatch.fnmatch(relpath, p):
            return True
        if p.endswith("/") and (relpath.startswith(p) or relpath + "/" == p):
            return True
    return False

count = 0
with zipfile.ZipFile("deploy_final.zip", "w", zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if not should_ignore(d + "/") and d != "__pycache__"]
        for file in files:
            fp = os.path.join(root, file)
            arc = os.path.relpath(fp, ".").replace("\\", "/")
            if not should_ignore(arc) and arc != "make_deploy.py":
                zf.write(fp, arc)
                print("+", arc)
                count += 1

print(f"\nTotal: {count} arquivos -> deploy_final.zip")
