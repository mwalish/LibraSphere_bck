from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import BranchRole, Member, MembershipTier, User
from branches.models import Branch, Library
from catalog.models import Creator, Edition, Item, ResourceType, Subject, Work, WorkCreator
from circulation.models import Policy

PASSWORD = "ChangeMe123!"
BOOKS = [
    ("Things Fall Apart", "Chinua Achebe", "Fiction"), ("Half of a Yellow Sun", "Chimamanda Ngozi Adichie", "Fiction"),
    ("Weep Not, Child", "Ngũgĩ wa Thiong'o", "Fiction"), ("Born a Crime", "Trevor Noah", "Memoir"),
    ("Sapiens", "Yuval Noah Harari", "History"), ("Educated", "Tara Westover", "Memoir"),
    ("The Overstory", "Richard Powers", "Fiction"), ("Braiding Sweetgrass", "Robin Wall Kimmerer", "Nature"),
    ("Project Hail Mary", "Andy Weir", "Science fiction"), ("The Song of Achilles", "Madeline Miller", "Fiction"),
    ("Atomic Habits", "James Clear", "Self-help"), ("Python Crash Course", "Eric Matthes", "Technology"),
]


class Command(BaseCommand):
    help = "DEV ONLY: demo library with two branches, sample catalog and one user per role."

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("seed_demo only runs with DJANGO_DEBUG=1")
        lib, _ = Library.objects.get_or_create(name="LibraSphere Demo Library")
        cbd, _ = Branch.objects.get_or_create(code="CBD", defaults={"library": lib, "name": "City Centre"})
        wlb, _ = Branch.objects.get_or_create(code="WLB", defaults={"library": lib, "name": "Westlands"})
        book, _ = ResourceType.objects.get_or_create(code="book", defaults={"name": "Book"})
        ResourceType.objects.get_or_create(code="ebook", defaults={"name": "E-book"})
        ResourceType.objects.get_or_create(code="journal", defaults={"name": "Journal",
                                           "metadata_template": {"required": ["volume", "issue"]}})
        basic, _ = MembershipTier.objects.get_or_create(name="Basic", defaults={"loan_limit": 3, "hold_limit": 2})
        MembershipTier.objects.get_or_create(name="Premium", defaults={"loan_limit": 8, "hold_limit": 5,
                                                                        "price": Decimal("1000")})
        Policy.objects.get_or_create(branch=None, tier=None, resource_type=None)

        if not Work.objects.exists():
            for i, (title, author, subject) in enumerate(BOOKS):
                w = Work.objects.create(title=title, language="en")
                creator, _ = Creator.objects.get_or_create(name=author, defaults={"sort_name": author})
                WorkCreator.objects.create(work=w, creator=creator)
                w.subjects.add(Subject.objects.get_or_create(name=subject)[0])
                ed = Edition.objects.create(work=w, resource_type=book)
                for branch in (cbd, wlb) if i % 3 else (cbd,):
                    for n in range(2):
                        Item.objects.create(edition=ed, branch=branch, shelf_location=f"{subject[:3].upper()}-{i + 1}",
                                            acquired_at=timezone.localdate() - timedelta(days=i * 5))
                w.refresh_search_text()

        def user(email, name, **kw):
            u, created = User.objects.get_or_create(email=email, defaults={"full_name": name, **kw})
            if created:
                u.set_password(PASSWORD)
                u.save()
            return u

        admin = user("admin@librasphere.test", "Site Admin", is_staff=True, is_superuser=True)
        mgr = user("manager@librasphere.test", "Mary Manager", is_staff=True)
        BranchRole.objects.get_or_create(user=mgr, branch=cbd, defaults={"role": "branch_manager"})
        lib_user = user("librarian@librasphere.test", "Leo Librarian")
        BranchRole.objects.get_or_create(user=lib_user, branch=cbd, defaults={"role": "librarian"})
        mem = user("member@librasphere.test", "Amina Member")
        Member.objects.get_or_create(user=mem, defaults={"home_branch": cbd, "tier": basic, "status": "active",
                                                         "expires_at": timezone.now() + timedelta(days=365)})
        self.stdout.write(self.style.SUCCESS(
            f"Demo data ready. Logins (password {PASSWORD}): admin@, manager@, librarian@, member@librasphere.test"))
