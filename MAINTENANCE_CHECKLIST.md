# Order Redirection Implementation - Maintenance Checklist

## For Developers Modifying This Implementation

### ✅ Before Making Changes

- [ ] Read INSPECTION_REPORT.md to understand the current state
- [ ] Read DEVELOPER_REFERENCE.md for implementation details
- [ ] Review all 4 redirect functions in views.py (lines 4844-5660)
- [ ] Understand how metadata is captured and stored
- [ ] Check template display logic (order_detail.html lines 1301-1345)

### ✅ When Adding New Capture Fields

1. **Identify all 4 places where old_customer_details is captured:**
   - [ ] `redirect_order_save()` - Line 4857
   - [ ] `redirect_rtv_save()` - Line 5365 (local order)
   - [ ] `redirect_rtv_save()` - Line 5398 (matched order)
   - [ ] `redirect_order_to_ncm()` - Receives via parameter

2. **Add new field to capture:**
   ```python
   _old_customer_details = {
       'customer_name': order.customer_name,
       'customer_phone': order.customer_phone,
       'shipping_address': order.shipping_address,
       'branch_city': order.branch_city,
       'new_field': order.new_field,  # ADD HERE
   }
   ```

3. **Update template to display:**
   ```django
   {% if log.metadata.new_field %}
   <div class="detail-row">
       <span class="detail-label"><i class="fas fa-icon"></i> New Field:</span>
       <span class="detail-value">{{ log.metadata.new_field }}</span>
   </div>
   {% endif %}
   ```

4. **Add CSS if needed:**
   ```css
   /* Update .old-customer-details if changing grid layout */
   .old-customer-details {
       display: grid;
       grid-template-columns: 1fr 1fr; /* Adjust columns if needed */
   }
   ```

5. **Run tests:**
   - [ ] `python test_order_redirection.py`
   - [ ] Manual test with actual order redirection
   - [ ] Test with mobile devices

### ✅ When Modifying Redirect Functions

1. **Ensure old details are captured BEFORE updates:**
   ```python
   # CORRECT: Capture first
   old_details = {...}
   order.field = new_value  # Update second

   # WRONG: Update first
   order.field = new_value
   old_details = {...}  # Captures new value!
   ```

2. **Always pass to activity log creation:**
   ```python
   OrderActivityLog.objects.create(
       order=order,
       action_type='redirected',
       metadata=old_customer_details or {},  # Always include
       ...
   )
   ```

3. **Test all 3 redirect paths:**
   - [ ] Direct order edit + NCM redirect
   - [ ] RTV standalone redirect
   - [ ] RTV with matched local order

### ✅ When Updating Template

1. **Always check for metadata existence:**
   ```django
   {% if log.action_type == 'redirected' and log.metadata %}
   ```

2. **Always check individual fields before displaying:**
   ```django
   {% if log.metadata.field_name %}
       <!-- Display field -->
   {% endif %}
   ```

3. **Maintain responsive design:**
   ```css
   @media (max-width: 768px) {
       .old-customer-details {
           grid-template-columns: 1fr;  /* Stack on mobile */
       }
   }
   ```

4. **Test on multiple screen sizes:**
   - [ ] Desktop (1920px)
   - [ ] Tablet (768px)
   - [ ] Mobile (375px)

### ✅ When Troubleshooting

**Metadata not saving:**
- [ ] Check if `OrderActivityLog.objects.create()` is being called
- [ ] Verify migration 0054 is applied: `python manage.py showmigrations | grep 0054`
- [ ] Check database: `SELECT * FROM dashboard_orderactivitylog WHERE action_type='redirected' LIMIT 1;`

**Metadata not displaying:**
- [ ] Check template condition: `{% if log.metadata %}`
- [ ] Verify metadata is not empty in database
- [ ] Check browser console for JavaScript errors
- [ ] Verify CSS is loading (inspect element)

**Old values showing new values:**
- [ ] Ensure old details are captured BEFORE order updates
- [ ] Check if correct variable is being passed to `create()`
- [ ] Verify order.save() happens AFTER metadata capture

**Mobile display broken:**
- [ ] Check if CSS media query is present
- [ ] Inspect element to verify grid-template-columns
- [ ] Test with Chrome DevTools mobile emulation

### ✅ Code Review Checklist

When reviewing changes to this implementation:

- [ ] Old customer details captured in ALL 4 places
- [ ] Captured BEFORE any order updates
- [ ] Metadata always passed to OrderActivityLog.create()
- [ ] Template has conditional checks for metadata existence
- [ ] Template checks individual fields before display
- [ ] CSS is responsive (2-col to 1-col)
- [ ] Error handling includes try/except for activity log creation
- [ ] No breaking changes to existing functions
- [ ] Database migrations are created if model changes
- [ ] Tests pass: `python test_order_redirection.py`
- [ ] Django system check passes: `python manage.py check`
- [ ] Manual testing completed on all redirect paths
- [ ] Mobile design verified

### ✅ Testing Scenarios

**Scenario 1: Basic Redirection**
```
1. Create order with customer details
2. Redirect via Possible Redirection page
3. Verify Activity Log shows old details
```

**Scenario 2: NULL Values**
```
1. Create order with some NULL customer fields
2. Redirect order
3. Verify template handles NULL values gracefully (doesn't show empty fields)
```

**Scenario 3: Matched Order Redirection**
```
1. Go to Possible Redirection
2. Select matched order
3. Redirect RTV order
4. Verify both matched and local orders have activity logs
5. Both should show old customer details
```

**Scenario 4: Mobile Display**
```
1. Open order detail on mobile/tablet
2. Scroll to Activity Log
3. Find redirected order
4. Verify old customer details section is visible
5. Verify layout is 1-column (not broken)
```

### ✅ Deployment Checklist

Before deploying changes:

- [ ] All tests pass
- [ ] Django check reports 0 errors
- [ ] Migrations are created and tested
- [ ] Code review completed
- [ ] Database backup created
- [ ] Deployment plan documented
- [ ] Rollback plan documented
- [ ] Notify stakeholders

### ✅ Performance Monitoring

After deployment, monitor:

- [ ] Database query performance (JSONField lookups)
- [ ] Activity log creation time
- [ ] Template rendering time (with large activity logs)
- [ ] JSON serialization/deserialization errors
- [ ] Database disk usage (JSON field size)

### ✅ Documentation Updates

When making changes, update:

- [ ] INSPECTION_REPORT.md (if changes affect verification)
- [ ] DEVELOPER_REFERENCE.md (if API changes)
- [ ] Code comments (in views.py and models.py)
- [ ] Docstrings (for modified functions)
- [ ] This checklist (if new scenarios discovered)

---

## Implementation Statistics

- **Lines of Code:** ~119 total
  - Models: 3 lines (metadata field)
  - Views: 45+ lines (capture logic across 3 functions)
  - Template: 45+ lines (display section + CSS)
  - Migrations: 1 file generated

- **Database Impact:**
  - 1 new column: `metadata` (JSON type)
  - 1 modified field: `action_type` (added 'redirected' choice)
  - No indexes needed (PostgreSQL auto-indexes JSON)
  - Average storage: 100-200 bytes per entry

- **Files Modified:**
  - `dashboard/models.py` (1 addition)
  - `dashboard/views.py` (3 functions enhanced)
  - `dashboard/templates/order_detail.html` (1 section added)
  - `dashboard/migrations/0054_*.py` (1 new migration)

---

## Known Limitations

1. **Metadata Storage:** Only stores customer-related fields. Additional fields require code changes.
2. **Historical Data:** Only captures old details from the moment of redirection onwards. No retroactive capture.
3. **Database Size:** JSON storage increases database size. Monitor disk usage in high-volume scenarios.
4. **Search:** Currently no full-text search on metadata fields. Can be added with database indexes if needed.

---

## Future Enhancements

Possible improvements for future versions:

1. **Capture Additional Fields:**
   - Payment details (before/after)
   - Discount amount
   - Shipping charges
   - Tax information

2. **Enhanced Display:**
   - Side-by-side comparison (old vs new)
   - Highlight changes
   - Diff visualization

3. **Audit Trail:**
   - Full order history timeline
   - Multiple redirects tracking
   - Cumulative changes view

4. **Export/Reporting:**
   - Export redirection history
   - Redirection analytics
   - Customer change tracking reports

---

## Contact & Support

For questions about this implementation:
1. Review INSPECTION_REPORT.md
2. Check DEVELOPER_REFERENCE.md
3. Run test_order_redirection.py
4. Check Django logs in `logs/` directory
5. Review related documentation files

---

**Last Updated:** May 19, 2026
**Version:** 1.0 (Production Release)
**Status:** ✅ Stable & Maintained
