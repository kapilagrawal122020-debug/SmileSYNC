from django.contrib import admin

from .models import ListDefinition, ListValue

admin.site.register([ListDefinition, ListValue])
