import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'myproject.settings')
django.setup()

from dashboard.models import GlobalNotice
from django.utils import timezone
from django.contrib.auth import get_user_model
User = get_user_model()
u = User.objects.first()
try:
    GlobalNotice.objects.create(content='test', display_until=timezone.now(), display_from=timezone.now(), created_by=u)
except Exception as e:
    print('ERROR_STR_IS:', str(e))
