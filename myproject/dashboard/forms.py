from django import forms
from .models import Product, Order, Category, ProductAttribute, ProductAttributeValue, ProductVariation, ProductImage, Customer


class ProductForm(forms.ModelForm):
    # Add variant and size fields
    variant_options = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. Red, Green, Blue'
        }),
        label='Choose Variant',
        help_text='Enter color/style options separated by commas'
    )
    
    size_options = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'e.g. M, X, XL'
        }),
        label='Choose Size',
        help_text='Enter size options separated by commas'
    )
    
    class Meta:
        model = Product
        fields = ['name', 'slug', 'description', 'category', 'product_type',
                  'price', 'cost_price', 'cost_price_type', 'stock', 'stock_status', 'image', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'product_type': forms.Select(attrs={'class': 'form-select'}),
            'price': forms.NumberInput(attrs={'class': 'form-control'}),
            'cost_price': forms.NumberInput(attrs={'class': 'form-control'}),
            'cost_price_type': forms.HiddenInput(),
            'stock': forms.NumberInput(attrs={'class': 'form-control'}),
            'stock_status': forms.Select(attrs={'class': 'form-select'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
class ProductVariationForm(forms.ModelForm):
    class Meta:
        model = ProductVariation
        fields = ['variation_name', 'sku', 'price', 'stock', 'status', 'is_active', 'image']  # ✅ Added variation_name
        widgets = {
            'variation_name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g., Red, Medium'
            }),
            'sku': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'PRD-001-VAR-A'  # ✅ CHANGED FROM TSHIRT-RED-M
            }),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'stock': forms.NumberInput(attrs={'class': 'form-control'}),
            'status': forms.Select(attrs={'class': 'form-select'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
        }
     
        
ProductVariationFormSet = forms.inlineformset_factory(
    Product,
    ProductVariation,
    form=ProductVariationForm,
    extra=0,
    can_delete=True,
    min_num=0,
    validate_min=False,
)

class ProductAttributeForm(forms.ModelForm):
    class Meta:
        model = ProductAttribute
        fields = ['name']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Size, Color, etc.'})
        }

class ProductAttributeValueForm(forms.ModelForm):
    class Meta:
        model = ProductAttributeValue
        fields = ['attribute', 'value']
        widgets = {
            'attribute': forms.Select(attrs={'class': 'form-select'}),
            'value': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Small, Red, etc.'})
        }


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ['name', 'slug', 'description', 'category', 'product_type',
                  'price', 'cost_price', 'cost_price_type', 'stock', 'stock_status', 'image', 'is_active']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control'}),
            'slug': forms.TextInput(attrs={'class': 'form-control'}),
            'description': forms.Textarea(attrs={'class': 'form-control', 'rows': 4}),
            'category': forms.Select(attrs={'class': 'form-select'}),
            'product_type': forms.Select(attrs={'class': 'form-select'}),
            'price': forms.NumberInput(attrs={'class': 'form-control'}),
            'cost_price': forms.NumberInput(attrs={'class': 'form-control'}),
            'cost_price_type': forms.HiddenInput(),
            'stock': forms.NumberInput(attrs={'class': 'form-control'}),
            'stock_status': forms.Select(attrs={'class': 'form-select'}),
            'image': forms.FileInput(attrs={'class': 'form-control'}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }


class ProductImageForm(forms.ModelForm):
    class Meta:
        model = ProductImage
        fields = ['image', 'alt_text', 'is_featured', 'order']
        widgets = {
            'image': forms.FileInput(attrs={'class': 'form-control', 'accept': 'image/*'}),
            'alt_text': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Image description (optional)'}),
            'is_featured': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'order': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'value': 0}),
        }

class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['name', 'phone', 'email', 'city', 'address', 'landmark']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Customer Name'}),
            'phone': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone Number'}),
            'email': forms.EmailInput(attrs={'class': 'form-control', 'placeholder': 'Email Address'}),
            'city': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'City'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3, 'placeholder': 'Full Address'}),
            'landmark': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Nearby Landmark'}),
        }


# Nepal Cities/Branches
NEPAL_CITIES = [
    ('', 'Select Branch/City'),
    ('Kathmandu', 'Kathmandu'),
    ('Pokhara', 'Pokhara'),
    ('Lalitpur', 'Lalitpur'),
    ('Bhaktapur', 'Bhaktapur'),
    ('Biratnagar', 'Biratnagar'),
    ('Birgunj', 'Birgunj'),
    ('Dharan', 'Dharan'),
    ('Hetauda', 'Hetauda'),
    ('Butwal', 'Butwal'),
    ('Other', 'Other'),
]        
        
class OrderForm(forms.ModelForm):
    class Meta:
        model = Order
        # ✅ UPDATED: Added payment_setup and status_setup fields
        fields = ['order_from', 'order_status', 'payment_setup', 'status_setup', 'branch_city', 'in_out']
        widgets = {
            'order_from': forms.Select(attrs={'class': 'form-select'}),
            'order_status': forms.Select(attrs={'class': 'form-select'}),
            'payment_setup': forms.Select(attrs={'class': 'form-select'}),  # ✅ NEW: Added CSS class
            'status_setup': forms.Select(attrs={'class': 'form-select'}),  # ✅ NEW: Added CSS class
            'branch_city': forms.Select(attrs={'class': 'form-select'}),
            'in_out': forms.Select(attrs={'class': 'form-select'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Add choices for order_from
        self.fields['order_from'].choices = [
            ('', 'Select Source'),
            ('website', 'Website'),
            ('facebook', 'Facebook'),
            ('instagram', 'Instagram'),
            ('phone', 'Phone'),
            ('walk-in', 'Walk-in'),
        ]
        # Add choices for order_status
        self.fields['order_status'].choices = [
            ('processing', 'Processing'),
            ('confirmed', 'Confirmed'),
            ('shipped', 'Shipped'),
            ('delivered', 'Delivered'),
            ('cancelled', 'Cancelled'),
        ]
        
        # ✅ NEW: Populate payment_setup from Setup model (Payment Setup)
        from .models import Setup
        payment_setups = Setup.objects.filter(setup_type='payment', is_active=True)
        self.fields['payment_setup'].queryset = payment_setups
        self.fields['payment_setup'].empty_label = "Select Payment Method"
        # ✅ Ensure CSS class is applied
        self.fields['payment_setup'].widget.attrs.update({'class': 'form-select'})
        
        # ✅ NEW: Populate status_setup from Setup model (Status Setup)
        status_setups = Setup.objects.filter(setup_type='status', is_active=True)
        self.fields['status_setup'].queryset = status_setups
        self.fields['status_setup'].empty_label = "Select Order Status"
        # ✅ Ensure CSS class is applied
        self.fields['status_setup'].widget.attrs.update({'class': 'form-select'})
        
        # ✅ UPDATED: Changed from 'city' to 'branch_city' choices
        self.fields['branch_city'].choices = NEPAL_CITIES
        
        # ✅ ADDED: Choices for in_out field
        self.fields['in_out'].choices = [
            ('in', 'IN'),
            ('out', 'OUT'),
        ]