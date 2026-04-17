from rest_framework import serializers


class WooCommerceOrderSerializer(serializers.Serializer):
    order_id = serializers.IntegerField()
    status = serializers.CharField(max_length=50)
    currency = serializers.CharField(max_length=10, default='NPR')
    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    billing = serializers.DictField(required=False, default=dict)
    shipping = serializers.DictField(required=False, default=dict)
    line_items = serializers.ListField(required=False, default=list)

    def validate_order_id(self, value):
        if value <= 0:
            raise serializers.ValidationError('order_id must be a positive integer.')
        return value

    def validate_total(self, value):
        if value < 0:
            raise serializers.ValidationError('total cannot be negative.')
        return value
