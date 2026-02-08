# NCM Branches Page - Quick Reference

## 📍 Page URL
```
http://yoursite.com/api/ncm-branches/
```

## 🎯 What It Does
- ✅ Fetches all NCM branches from the NCM API
- ✅ Displays them in a searchable, sortable table
- ✅ Shows all branch details (Name, Code, Phone, Location, Address, etc.)
- ✅ Allows copying branch codes to clipboard
- ✅ Links to Google Maps for location viewing
- ✅ Links to phone dialer for calling

## 📊 Fields Displayed
1. **Branch Name** - Full name of the branch
2. **Branch Code** - Unique identifier (copyable)
3. **Areas Covered** - Service areas
4. **Municipality** - City/municipal area
5. **District** - District name
6. **Region** - Region/zone
7. **Phone** - Contact number (clickable)
8. **Coordinates** - GPS location (Google Maps link)
9. **Address** - Full branch address

## 🔧 Files Modified/Created

### Modified Files:
1. **`dashboard/views.py`** (Line 6166)
   - Updated `ncm_branches_json()` view
   - Enhanced field extraction from API
   - Better error handling

2. **`dashboard/templates/ncm_branches.html`**
   - Changed from card grid to professional table
   - Added checkboxes for bulk selection
   - Added all new fields
   - Updated JavaScript for table handling

### Existing Files (Already in place):
- `dashboard/urls.py` - URL route already configured
- `services/ncm_service.py` - NCM service class

## 🚀 How to Access

### For Users:
1. Login to admin dashboard
2. Click on "NCM Branches" menu item (if added)
3. Or navigate directly to: `/api/ncm-branches/`

### For Developers:
1. Import the view:
   ```python
   from dashboard.views import ncm_branches_json
   ```

2. Use the JSON API:
   ```javascript
   fetch('/api/ncm-branches/?format=json')
     .then(r => r.json())
     .then(data => console.log(data.branches))
   ```

## 🔌 API Credentials Required

Add to `settings.py`:
```python
NCM_API_BASE_URL = 'https://portal.nepalcanmove.com/api'
NCM_API_BASE_URL_V2 = 'https://portal.nepalcanmove.com/api/v2'
NCM_API_KEY = 'your_ncm_api_token'
```

## 🎛️ Features

### Search
- Type branch name or code
- Real-time filtering
- Clear button to reset

### Sort
- A→Z (alphabetical)
- Z→A (reverse)
- Code ascending/descending

### Actions
- ☑️ Select multiple branches
- 📋 Copy branch codes
- 🗺️ View on map
- 📞 Call directly

## 🔍 API Response Handling

The view automatically maps these field variations:
- `code` or `Code` → Branch Code
- `name` or `Name` → Branch Name
- `areas_covered` or `Areas_Covered` → Areas Covered
- `municipality` or `Municipality` → Municipality
- And 5 more fields...

## ⚡ Quick Testing

### Test JSON API:
```bash
curl "http://yoursite.com/api/ncm-branches/?format=json" \
     -H "Cookie: sessionid=your_session_id"
```

### Test HTML Page:
Just visit: `http://yoursite.com/api/ncm-branches/`

## 🐛 If Something Doesn't Work

1. **Check credentials:**
   ```python
   # In Django shell
   from django.conf import settings
   print(settings.NCM_API_KEY)
   print(settings.NCM_API_BASE_URL_V2)
   ```

2. **Test API directly:**
   ```bash
   curl -H "Authorization: Token YOUR_KEY" \
        https://portal.nepalcanmove.com/api/v2/branches
   ```

3. **Check browser console:**
   Press F12 → Console tab → Look for errors

4. **Check Django logs:**
   ```bash
   tail -f logs/django.log
   ```

## 📱 Mobile Support

✅ Fully responsive
- Desktop: Full table view
- Tablet: Horizontal scroll
- Mobile: Stacked columns or horizontal scroll

## 🔐 Security

- ✅ Login required
- ✅ API key protected
- ✅ CSRF protected
- ✅ Input sanitized

## 💾 Data Freshness

Data is fetched fresh from NCM API on each page load.
To add caching:
```python
from django.views.decorators.cache import cache_page

@cache_page(60 * 60)  # Cache for 1 hour
def ncm_branches_json(request):
    ...
```

## 📈 Usage Examples

### In Order Creation Form:
```html
<select id="ncm-branch" name="ncm_destination_branch">
  <option value="">Select Branch...</option>
  <!-- Load from /api/ncm-branches/?format=json -->
</select>
```

### In Admin Dashboard:
```html
<a href="{% url 'ncm_branches_json' %}" class="btn btn-primary">
  View NCM Branches
</a>
```

### As Shipping Calculator:
```javascript
const branches = await fetch('/api/ncm-branches/?format=json')
  .then(r => r.json())
  .then(d => d.branches);

const branch = branches.find(b => b.code === selectedCode);
console.log(`Selected: ${branch.name} in ${branch.district}`);
```

## 🎓 Learning Resources

- View source: `dashboard/templates/ncm_branches.html`
- View code: `dashboard/views.py` line 6166
- Service class: `services/ncm_service.py`
- API docs: NCM Portal documentation

## 💡 Pro Tips

1. **Copy codes easily** - Click the copy button, no manual typing
2. **Find by code** - Search for branch code (faster)
3. **Check phone** - Click number to call on mobile
4. **Save location** - Click map link to save in Google Maps
5. **Bulk select** - Check top checkbox to select all visible branches

## 🎉 You're Ready!

The page is fully functional. Start using it at:
```
http://yoursite.com/api/ncm-branches/
```

Questions? Check the detailed guide: `NCM_BRANCHES_IMPLEMENTATION.md`
