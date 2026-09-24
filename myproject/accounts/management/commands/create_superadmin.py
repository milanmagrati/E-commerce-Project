from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
import os

User = get_user_model()

class Command(BaseCommand):
    help = 'Creates a superadmin user'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            type=str,
            default='admin',
            help='Username for the superadmin'
        )
        parser.add_argument(
            '--email',
            type=str,
            default='admin@example.com',
            help='Email for the superadmin'
        )
        parser.add_argument(
            '--password',
            type=str,
            default='admin123',
            help='Password for the superadmin'
        )

    def handle(self, *args, **options):
        username = options['username']
        email = options['email']
        password = options['password']

        if User.objects.filter(username=username).exists():
            self.stdout.write(
                self.style.WARNING(f"Superadmin user '{username}' already exists!")
            )
            return

        user = User.objects.create_superuser(
            username=username,
            email=email,
            password=password
        )

        self.stdout.write(
            self.style.SUCCESS(f"✓ Superadmin user '{username}' created successfully!")
        )
        self.stdout.write(f"  Username: {username}")
        self.stdout.write(f"  Email: {email}")
