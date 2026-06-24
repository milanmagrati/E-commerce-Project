import os
import django
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from accounts.models import Role

defaults = {r.name: r.default_permissions for r in Role.objects.all()}
print(json.dumps(defaults, indent=2))
