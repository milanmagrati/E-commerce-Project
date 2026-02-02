
import os
import django
from django.conf import settings
from django.template import Engine

# Configure minimal Django settings
if not settings.configured:
    settings.configure(
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'APP_DIRS': True,
        }],
        INSTALLED_APPS=['django.contrib.auth', 'django.contrib.contenttypes'],
    )
    django.setup()

def check_template(path):
    with open(path, 'r') as f:
        content = f.read()
    
    print(f"Checking {path}...")
    try:
        engine = Engine.get_default()
        template = engine.from_string(content)
        print("Template parsed successfully!")
    except Exception as e:
        print(f"Error parsing template: {e}")
        # Identify the line number
        if hasattr(e, 'token'):
            print(f"Error at token: {e.token}")
            print(f"Line number: {e.token.lineno}")

if __name__ == '__main__':
    check_template('/home/milan-magrati/Desktop/EcommerceAdmin/myproject/templates/base.html')
