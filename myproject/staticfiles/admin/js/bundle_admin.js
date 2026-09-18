(function($) {
    'use strict';

    function toggleBundleInlines() {
        var productType = $('#id_product_type').val();
        var bundleInline = $('.inline-group:has([id*="bundlecomponent"])');
        var purchaseInline = $('.inline-group:has([id*="productpurchase"])');

        if (productType === 'bundle') {
            bundleInline.show();
            purchaseInline.hide();
        } else {
            bundleInline.hide();
            purchaseInline.show();
        }
    }

    $(document).ready(function() {
        toggleBundleInlines();
        $('#id_product_type').on('change', toggleBundleInlines);
    });
})(django.jQuery);
