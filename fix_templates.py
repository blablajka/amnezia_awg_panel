import os
import re

TEMPLATES_DIR = "web/templates"

def fix_template(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    # Replace href="/dashboard" with href="{{ admin_path }}/dashboard"
    # Match href="/..." but not href="//..." and not href="/static/..."
    content = re.sub(r'href="/(?!(static|/))([^"]*)"', r'href="{{ admin_path }}/\2"', content)
    
    # Replace action="/login" with action="{{ admin_path }}/login"
    content = re.sub(r'action="/(?!(static|/))([^"]*)"', r'action="{{ admin_path }}/\2"', content)

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

for filename in os.listdir(TEMPLATES_DIR):
    if filename.endswith(".html"):
        fix_template(os.path.join(TEMPLATES_DIR, filename))

print("Templates updated successfully.")
