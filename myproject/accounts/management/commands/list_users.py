from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'List all users in the system'

    def handle(self, *args, **options):
        users = User.objects.all()
        
        if not users.exists():
            self.stdout.write(self.style.WARNING("No users found in the system"))
            return

        self.stdout.write(self.style.SUCCESS("\n=== Users in System ===\n"))
        
        for user in users:
            is_admin = "✓ Admin" if user.is_staff else ""
            is_superuser = "✓ Superuser" if user.is_superuser else ""
            self.stdout.write(f"• {user.username} ({user.email}) {is_admin} {is_superuser}")
        
        self.stdout.write(self.style.SUCCESS(f"\nTotal: {users.count()} user(s)\n"))
