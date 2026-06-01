import os
import re

ROUTERS_DIR = "web/routers"

def fix_router(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Add import for settings if not present
    if "from config import settings" not in content:
        content = re.sub(r'(from fastapi import.*)', r'\1\nfrom config import settings', content)

    # 2. Fix RedirectResponse("/login")
    content = re.sub(r'RedirectResponse\("/login"', r'RedirectResponse(f"{settings.ADMIN_PATH}/login"', content)

    # 3. Fix RedirectResponse("/some_path") -> RedirectResponse(f"{settings.ADMIN_PATH}/some_path")
    # We only match literal strings that start with /
    content = re.sub(r'RedirectResponse\("/(?!login)([^"]+)"', r'RedirectResponse(f"{settings.ADMIN_PATH}/\1"', content)

    # 4. Add admin_path to TemplateResponse context if not present
    # Usually it looks like: {"request": request, "page": "dashboard"}
    # We can inject "admin_path": settings.ADMIN_PATH into the dictionary.
    if '"admin_path": settings.ADMIN_PATH' not in content:
        content = re.sub(r'({"request": request,)', r'\1 "admin_path": settings.ADMIN_PATH,', content)
        content = re.sub(r'({"request": request})', r'{"request": request, "admin_path": settings.ADMIN_PATH}', content)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

for filename in os.listdir(ROUTERS_DIR):
    if filename.endswith(".py") and filename != "__init__.py":
        fix_router(os.path.join(ROUTERS_DIR, filename))

print("Routers updated successfully.")
