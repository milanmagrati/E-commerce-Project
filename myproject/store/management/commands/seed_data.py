import random
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from dashboard.models import Product
from store.models import ProductReview

User = get_user_model()

REVIEW_COMMENTS = [
    'Great product! Exactly as described.',
    'Very happy with this purchase. Fast delivery too.',
    'Good quality for the price. Would recommend.',
    'Exceeded my expectations. Will buy again.',
    'Decent product but packaging could be better.',
    'Amazing value for money!',
    'Works perfectly. Very satisfied.',
    'Nice product, good build quality.',
    'Arrived on time and works great.',
    'Love it! Already recommended to friends.',
    'Pretty good overall. Minor issues but nothing major.',
    'Excellent quality. Five stars!',
]

FIRST_NAMES = ['Ram', 'Sita', 'Hari', 'Gita', 'Bikash', 'Anita', 'Suresh', 'Kamala', 'Rajesh', 'Sunita']
LAST_NAMES = ['Sharma', 'Thapa', 'Gurung', 'Maharjan', 'Shrestha', 'Tamang', 'Rai', 'Karki', 'Poudel', 'KC']


class Command(BaseCommand):
    help = 'Seeds store reviews for existing dashboard products'

    def handle(self, *args, **options):
        self.stdout.write('Seeding store review data...')

        # Create demo users for reviews
        demo_users = []
        for i in range(10):
            first = FIRST_NAMES[i]
            last = LAST_NAMES[i]
            username = f'{first.lower()}.{last.lower()}'
            email = f'{username}@example.com'
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'email': email,
                    'first_name': first,
                    'last_name': last,
                }
            )
            if created:
                user.set_password('demo1234')
                user.save()
            demo_users.append(user)

        # Create reviews for existing dashboard products
        products = Product.objects.filter(is_active=True, is_deleted=False)
        review_count = 0
        for product in products:
            reviewers = random.sample(demo_users, k=min(random.randint(2, 6), len(demo_users)))
            for reviewer in reviewers:
                _, created = ProductReview.objects.get_or_create(
                    product=product,
                    user=reviewer,
                    defaults={
                        'rating': random.choices([3, 4, 5], weights=[1, 3, 4])[0],
                        'comment': random.choice(REVIEW_COMMENTS),
                    }
                )
                if created:
                    review_count += 1

        self.stdout.write(self.style.SUCCESS(
            f'Created {review_count} reviews for {products.count()} products'
        ))
