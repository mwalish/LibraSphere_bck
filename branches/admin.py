from django.contrib import admin

from .models import Branch, BranchClosure, Library

admin.site.register([Library, Branch, BranchClosure])
