from django.core.management.base import BaseCommand
from dashboard.models import Customer, Order
from django.db.models import Count

class Command(BaseCommand):
    help = "Merge duplicate customers with same phone number"

    def handle(self, *args, **options):
        self.stdout.write("=== Fixing duplicate customers ===")
        duplicates = Customer.objects.values("phone").annotate(count=Count("id")).filter(count__gt=1)
        for dup in duplicates:
            phone = dup["phone"]
            customers = list(Customer.objects.filter(phone=phone).order_by("id"))
            primary = customers[0]
            self.stdout.write(f"Phone {phone}: keeping id={primary.id} ({primary.name}), merging {len(customers)-1} duplicate(s)")
            for duplicate in customers[1:]:
                order_count = Order.objects.filter(customer=duplicate).count()
                Order.objects.filter(customer=duplicate).update(customer=primary)
                self.stdout.write(f"  Moved {order_count} orders from id={duplicate.id} to id={primary.id}")
                duplicate.delete()
                self.stdout.write(f"  Deleted duplicate id={duplicate.id}")
        temp_customers = Customer.objects.filter(phone="9863665421")
        for tc in temp_customers:
            if Order.objects.filter(customer=tc).count() == 0:
                tc.delete()
                self.stdout.write(f"Deleted temp customer 9863665421 id={tc.id}")
            else:
                self.stdout.write(f"WARNING: 9863665421 has orders, skipping")
        self.stdout.write(self.style.SUCCESS("=== Done! ==="))
