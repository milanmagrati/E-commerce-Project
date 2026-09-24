from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()

class Command(BaseCommand):
    help = 'Changes password for an existing user'

    def add_arguments(self, parser):
        parser.add_argument(
            'username',
            type=str,
            help='Username of the user'
        )
        parser.add_argument(
            'password',
            type=str,
            help='New password for the user'
        )

    def handle(self, *args, **options):
        username = options['username']
        password = options['password']

        try:
            user = User.objects.get(username=username)
            user.set_password(password)
            user.save()
            self.stdout.write(
                self.style.SUCCESS(f"✓ Password for '{username}' changed successfully!")
            )
        except User.DoesNotExist:
            self.stdout.write(
                self.style.ERROR(f"✗ User '{username}' does not exist!")
            )
