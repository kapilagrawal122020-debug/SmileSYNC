from django.contrib import admin

from .models import StockItem, StockMovement, UsageRule

admin.site.register([StockItem, UsageRule, StockMovement])
