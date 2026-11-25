from django.contrib import admin

from .models import Fine, Payment

admin.site.register([Fine, Payment])
