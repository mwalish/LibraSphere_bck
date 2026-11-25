import secrets

from django.db import models

from core.models import TimeStamped


class ResourceType(models.Model):
    """book, ebook, audiobook, journal, magazine, multimedia ... with a metadata template."""
    code = models.SlugField(unique=True)
    name = models.CharField(max_length=60)
    # {"required": ["issue", "volume"]} -> keys that Edition.metadata must contain
    metadata_template = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return self.name


class Creator(models.Model):
    name = models.CharField(max_length=200, unique=True)
    sort_name = models.CharField(max_length=200, blank=True)

    class Meta:
        ordering = ["sort_name", "name"]

    def __str__(self):
        return self.name


class Subject(models.Model):
    name = models.CharField(max_length=100, unique=True)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="children")

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Work(TimeStamped):
    """The intellectual work. Editions and physical items hang off it."""
    title = models.CharField(max_length=300)
    subtitle = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    language = models.CharField(max_length=8, default="en", db_index=True)
    first_published = models.PositiveSmallIntegerField(null=True, blank=True)
    cover_url = models.URLField(max_length=500, blank=True)
    creators = models.ManyToManyField(Creator, through="WorkCreator", related_name="works")
    subjects = models.ManyToManyField(Subject, related_name="works", blank=True)
    # title + subtitle + creators + subjects + ISBNs. Target of the FULLTEXT index on MySQL.
    search_text = models.TextField(blank=True, editable=False)

    class Meta:
        ordering = ["title"]

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        if not self.search_text:
            self.search_text = f"{self.title} {self.subtitle}".strip()
        super().save(*args, **kwargs)

    def refresh_search_text(self):
        parts = [self.title, self.subtitle,
                 *self.creators.values_list("name", flat=True),
                 *self.subjects.values_list("name", flat=True),
                 *self.editions.exclude(isbn13__isnull=True).values_list("isbn13", flat=True)]
        self.search_text = " ".join(p for p in parts if p)
        Work.objects.filter(pk=self.pk).update(search_text=self.search_text)


class WorkCreator(models.Model):
    work = models.ForeignKey(Work, on_delete=models.CASCADE)
    creator = models.ForeignKey(Creator, on_delete=models.CASCADE)
    role = models.CharField(max_length=30, default="author")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["work", "creator", "role"], name="unique_work_creator_role")]


class Edition(TimeStamped):
    work = models.ForeignKey(Work, on_delete=models.CASCADE, related_name="editions")
    resource_type = models.ForeignKey(ResourceType, on_delete=models.PROTECT, related_name="editions")
    isbn13 = models.CharField(max_length=13, unique=True, null=True, blank=True)
    isbn10 = models.CharField(max_length=10, null=True, blank=True, db_index=True)
    publisher = models.CharField(max_length=150, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    language = models.CharField(max_length=8, default="en")
    pages = models.PositiveIntegerField(null=True, blank=True)
    cover_url = models.URLField(max_length=500, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    def __str__(self):
        return f"{self.work.title} ({self.resource_type.code})"


class Item(TimeStamped):
    """One physical copy (barcode on a shelf) at one branch."""
    class Status(models.TextChoices):
        AVAILABLE = "available", "Available"
        ON_LOAN = "on_loan", "On loan"
        HOLD_SHELF = "hold_shelf", "On hold shelf"
        IN_TRANSIT = "in_transit", "In transit"
        LOST = "lost", "Lost"
        DAMAGED = "damaged", "Damaged"
        WITHDRAWN = "withdrawn", "Withdrawn"

    edition = models.ForeignKey(Edition, on_delete=models.PROTECT, related_name="items")
    branch = models.ForeignKey("branches.Branch", on_delete=models.PROTECT, related_name="items")
    barcode = models.CharField(max_length=40, unique=True, blank=True)
    call_number = models.CharField(max_length=60, blank=True)
    shelf_location = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.AVAILABLE)
    acquired_at = models.DateField(null=True, blank=True)
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["barcode"]
        indexes = [models.Index(fields=["branch", "status"])]

    def __str__(self):
        return self.barcode

    def save(self, *args, **kwargs):
        if not self.barcode:
            while True:
                code = f"{self.branch.code}-{secrets.token_hex(4).upper()}"
                if not Item.objects.filter(barcode=code).exists():
                    self.barcode = code
                    break
        super().save(*args, **kwargs)


class DigitalAsset(models.Model):
    """Phase 3 placeholder so migrations don't change when the digital library lands."""
    edition = models.ForeignKey(Edition, on_delete=models.CASCADE, related_name="assets")
    kind = models.CharField(max_length=10)  # epub | pdf | mp3
    storage_key = models.CharField(max_length=300)
    concurrent_licenses = models.PositiveIntegerField(default=1)
