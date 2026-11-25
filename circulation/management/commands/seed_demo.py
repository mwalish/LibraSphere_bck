"""Rich demo data that exercises every feature. Built with the real services (checkout, holds, payments) so
the data is consistent, then back-dated so history, overdue items and fines look realistic.

    python manage.py seed_demo            # first time (skips if data exists)
    python manage.py seed_demo --reset    # wipe all library data and rebuild
"""
import random
from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.test.utils import override_settings
from django.utils import timezone

from accounts.models import BranchRole, Member, MembershipTier, User
from billing import services as billing
from billing.models import Fine, FineAllocation, Payment, Waiver
from branches.models import Branch, BranchClosure, Library
from catalog.models import Creator, Edition, Item, ResourceType, Subject, Work, WorkCreator
from circulation import services
from circulation.models import Hold, Loan, LoanEvent, Policy
from core.models import AuditLog
from notifications.models import Notification
from notifications.services import notify

PASSWORD = "ChangeMe123!"
DOMAIN = "librasphere.test"

# (title, authors, subject, year, one-line description). Descriptions are original demo text.
CATALOG = [
    ("Things Fall Apart", "Chinua Achebe", "Fiction", 1958, "A landmark novel of a village and the arrival of colonial rule."),
    ("Half of a Yellow Sun", "Chimamanda Ngozi Adichie", "Fiction", 2006, "Lives intertwine during the Nigerian civil war."),
    ("Weep Not, Child", "Ngũgĩ wa Thiong'o", "Fiction", 1964, "A boy's hopes for school against the backdrop of the Mau Mau uprising."),
    ("A Grain of Wheat", "Ngũgĩ wa Thiong'o", "Fiction", 1967, "Villagers confront secrets on the eve of Kenyan independence."),
    ("The River Between", "Ngũgĩ wa Thiong'o", "Fiction", 1965, "Two ridges, two traditions, and a young man caught between them."),
    ("Petals of Blood", "Ngũgĩ wa Thiong'o", "Fiction", 1977, "A murder investigation exposes a changing Kenya."),
    ("Americanah", "Chimamanda Ngozi Adichie", "Fiction", 2013, "Love and identity across Lagos, London and the United States."),
    ("The Overstory", "Richard Powers", "Fiction", 2018, "Nine lives bound together by trees."),
    ("The Song of Achilles", "Madeline Miller", "Fiction", 2011, "A retelling of the Trojan War through friendship and love."),
    ("Project Hail Mary", "Andy Weir", "Science fiction", 2021, "A lone astronaut must solve a problem that threatens Earth."),
    ("Kintu", "Jennifer Nansubuga Makumbi", "Fiction", 2014, "A Ugandan family saga stretching across generations."),
    ("Dust", "Yvonne Adhiambo Owuor", "Fiction", 2013, "A family in northern Kenya unravels its past."),
    ("Wizard of the Crow", "Ngũgĩ wa Thiong'o", "Fiction", 2006, "A satire of power in an imaginary African state."),
    ("Season of Migration to the North", "Tayeb Salih", "Fiction", 1966, "A returning student meets a mysterious stranger."),
    ("Born a Crime", "Trevor Noah", "Memoir", 2016, "Growing up in South Africa, told with humour."),
    ("Educated", "Tara Westover", "Memoir", 2018, "A memoir about the pull of family and the power of learning."),
    ("Unbowed", "Wangari Maathai", "Memoir", 2006, "The life of the Nobel laureate and founder of the Green Belt Movement."),
    ("Long Walk to Freedom", "Nelson Mandela", "Memoir", 1994, "An autobiography of struggle and reconciliation."),
    ("I Know Why the Caged Bird Sings", "Maya Angelou", "Memoir", 1969, "A childhood in the American South."),
    ("Sapiens", "Yuval Noah Harari", "History", 2011, "A sweeping history of humankind."),
    ("Guns, Germs, and Steel", "Jared Diamond", "History", 1997, "Why some societies came to dominate others."),
    ("The Gene", "Siddhartha Mukherjee", "Science", 2016, "An intimate history of heredity."),
    ("Cosmos", "Carl Sagan", "Science", 1980, "A tour of the universe and our place in it."),
    ("Braiding Sweetgrass", "Robin Wall Kimmerer", "Nature", 2013, "Indigenous wisdom and the science of plants."),
    ("Atomic Habits", "James Clear", "Self-help", 2018, "Small changes that compound into remarkable results."),
    ("Deep Work", "Cal Newport", "Self-help", 2016, "Rules for focused success in a distracted world."),
    ("Thinking, Fast and Slow", "Daniel Kahneman", "Psychology", 2011, "How two systems of thought shape our choices."),
    ("The Lean Startup", "Eric Ries", "Business", 2011, "Building products by testing ideas quickly."),
    ("Python Crash Course", "Eric Matthes", "Technology", 2015, "A hands-on introduction to programming in Python."),
    ("Clean Code", "Robert C. Martin", "Technology", 2008, "A handbook of agile software craftsmanship."),
    ("Designing Data-Intensive Applications", "Martin Kleppmann", "Technology", 2017, "The principles behind reliable, scalable data systems."),
    ("The Pragmatic Programmer", "Andrew Hunt; David Thomas", "Technology", 1999, "Practical advice for working programmers."),
    ("Eloquent JavaScript", "Marijn Haverbeke", "Technology", 2011, "A modern introduction to programming in JavaScript."),
    ("Matilda", "Roald Dahl", "Children", 1988, "A gifted girl and her magical mind."),
    ("Charlotte's Web", "E. B. White", "Children", 1952, "A pig, a spider and an unlikely friendship."),
    ("The Gruffalo", "Julia Donaldson", "Children", 1999, "A mouse outwits the creatures of the deep dark wood."),
    ("Where the Wild Things Are", "Maurice Sendak", "Children", 1963, "Max sails away to where the wild things are."),
]
# fictional periodicals (demo only): (title, subject, resource type, volume, issue)
PERIODICALS = [
    ("East African Journal of Library Science (demo)", "Library science", "journal", "12", "3"),
    ("Savannah Science Review (demo)", "Science", "journal", "8", "1"),
    ("The Reader's Monthly (demo)", "Magazines", "magazine", "2026", "11"),
    ("Nature Notes Quarterly (demo)", "Nature", "magazine", "2026", "Q4"),
]
# copies per branch; anything not listed uses a rotating default layout
LAYOUT = {
    "Half of a Yellow Sun": {"CBD": 1}, "Project Hail Mary": {"CBD": 1}, "Atomic Habits": {"CBD": 2},
    "Educated": {"CBD": 2, "WLB": 1}, "Sapiens": {"CBD": 2, "KCL": 1}, "Deep Work": {"CBD": 1},
    "Clean Code": {"CBD": 2, "WLB": 1}, "Cosmos": {"CBD": 1, "WLB": 1}, "The Gruffalo": {"WLB": 2, "CBD": 1},
    "Charlotte's Web": {"WLB": 2}, "Matilda": {"WLB": 2, "KCL": 1}, "Where the Wild Things Are": {"KCL": 2, "WLB": 1},
    "Guns, Germs, and Steel": {"KCL": 1, "CBD": 1}, "Thinking, Fast and Slow": {"CBD": 1, "WLB": 1},
    "Designing Data-Intensive Applications": {"CBD": 1, "WLB": 1}, "Dust": {"CBD": 1}, "Kintu": {"CBD": 1, "WLB": 2},
    "Braiding Sweetgrass": {"CBD": 1, "KCL": 1}, "The Pragmatic Programmer": {"CBD": 1, "WLB": 1},
}
DEFAULT_LAYOUTS = [{"CBD": 1}, {"CBD": 2, "WLB": 1}, {"CBD": 1, "WLB": 1, "KCL": 1}, {"WLB": 2, "KCL": 1}]


def isbn13(n):
    body = f"97819{n:07d}"
    check = (10 - sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(body)) % 10) % 10
    return body + str(check)


class Command(BaseCommand):
    help = "DEV: build a rich demo library (3 branches, ~40 titles, members in every state, loans, holds, fines). --reset deletes ALL catalog, loan, hold, fine and membership data first."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="delete all library data first and rebuild")
        parser.add_argument("--force", action="store_true", help="allow with DJANGO_DEBUG=0 (used by tests)")

    # ------------------------------------------------------------------ entry
    def handle(self, *args, **opts):
        if not (settings.DEBUG or opts["force"]):
            raise CommandError("seed_demo only runs with DJANGO_DEBUG=1")
        if opts["reset"]:
            self.wipe()
        elif Work.objects.exists() or Member.objects.exists():
            self.stdout.write("Library data already exists. Run  python manage.py seed_demo --reset  to rebuild it.")
            return
        # emails are swallowed and sent inline while seeding; nothing needs Redis
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True, EMAIL_BACKEND="django.core.mail.backends.dummy.EmailBackend"):
            self.now = timezone.now()
            self.rng = random.Random(42)
            self.build()
        self.cheat_sheet()

    # ------------------------------------------------------------------ wipe
    def wipe(self):
        for model in (FineAllocation, Waiver, Payment, Fine, LoanEvent, Loan, Hold, Notification, AuditLog, Item,
                      Edition, Work, Creator, Subject, Policy, BranchClosure):
            model.objects.all().delete()
        Member.objects.all().delete()                                 # membership profiles (user accounts stay)
        User.objects.filter(email__endswith=f"@{DOMAIN}").delete()   # demo users (and their branch roles)
        for model in (MembershipTier, Branch, Library, ResourceType):
            model.objects.all().delete()

    # ------------------------------------------------------------------ build
    def build(self):
        self.make_setup()
        self.make_people()
        self.make_catalog()
        self.random_history(70)
        self.scenarios()
        services.assess_all_fines()
        services.send_due_reminders()
        services.expire_holds()
        services.anonymize_history()
        self.finishing_touches()

    def eod(self, dt):
        return timezone.localtime(dt).replace(hour=23, minute=59, second=0, microsecond=0)

    # ---- setup: branches, formats, tiers, policies
    def make_setup(self):
        lib, _ = Library.objects.get_or_create(name="LibraSphere Demo Library")
        self.branches = {}
        for code, name, addr in (("CBD", "City Centre", "Kenyatta Avenue"), ("WLB", "Westlands", "Waiyaki Way"),
                                 ("KCL", "Community Library", "Kibera Drive")):
            self.branches[code], _ = Branch.objects.get_or_create(code=code, defaults={"library": lib, "name": name, "address": addr})
        for d, why in ((date(2026, 12, 12), "Jamhuri Day"), (date(2026, 12, 25), "Christmas Day"),
                       (date(2026, 12, 26), "Boxing Day"), (date(2027, 1, 1), "New Year's Day")):
            for b in self.branches.values():
                BranchClosure.objects.get_or_create(branch=b, date=d, defaults={"reason": why})
        self.rt = {}
        for code, name, tpl in (("book", "Book", {}), ("audiobook", "Audiobook", {}), ("ebook", "E-book", {}),
                                ("journal", "Journal", {"required": ["volume", "issue"]}),
                                ("magazine", "Magazine", {"required": ["volume", "issue"]})):
            self.rt[code], _ = ResourceType.objects.get_or_create(code=code, defaults={"name": name, "metadata_template": tpl})
        self.tier = {}
        for name, loans, holds, price in (("Basic", 3, 2, 0), ("Premium", 8, 5, 1000), ("Researcher", 12, 8, 2500)):
            self.tier[name], _ = MembershipTier.objects.get_or_create(
                name=name, defaults={"loan_limit": loans, "hold_limit": holds, "price": Decimal(price)})
        base = dict(loan_days=14, max_renewals=2, renew_days=14, fine_per_day=Decimal("10"), fine_cap=Decimal("500"),
                    grace_days=0, hold_pickup_days=3)
        # most specific row wins: branch + tier + format  >  branch  >  tier  >  format  >  everything
        for scope, over in (
            (dict(), {}),
            (dict(tier=self.tier["Premium"]), dict(loan_days=21, max_renewals=3, renew_days=21)),
            (dict(tier=self.tier["Researcher"]), dict(loan_days=28, max_renewals=4, renew_days=28)),
            (dict(resource_type=self.rt["journal"]), dict(loan_days=7, max_renewals=0, fine_per_day=Decimal("20"))),
            (dict(branch=self.branches["WLB"]), dict(loan_days=10)),
            (dict(branch=self.branches["WLB"], resource_type=self.rt["journal"]), dict(loan_days=5, max_renewals=0)),
        ):
            Policy.objects.get_or_create(**{"branch": None, "tier": None, "resource_type": None, **scope}, defaults={**base, **over})

    # ---- staff and members
    def mk_user(self, key, name, **flags):
        return User.objects.create_user(f"{key}@{DOMAIN}", PASSWORD, full_name=name, **flags)

    def mk_member(self, key, no, name, tier, branch, status="active", **extra):
        user = self.mk_user(key, name, phone=f"+2547{self.rng.randint(10000000, 99999999)}")
        expires = self.now + timedelta(days=300) if status in ("active", "suspended") else self.now - timedelta(days=20)
        return Member.objects.create(user=user, member_no=no, barcode=no, home_branch=self.branches[branch],
                                     tier=self.tier[tier] if tier else None, status=status, expires_at=expires, **extra)

    def make_people(self):
        self.admin = self.mk_user("admin", "Site Admin", is_staff=True, is_superuser=True)
        self.mgr_cbd = self.mk_user("manager", "Mary Manager (City Centre)", is_staff=True)
        self.mgr_wlb = self.mk_user("manager.westlands", "Wanjiku Manager (Westlands)", is_staff=True)
        self.lib = {"CBD": self.mk_user("librarian", "Leo Librarian (City Centre)"),
                    "WLB": self.mk_user("librarian.westlands", "Wafula Librarian (Westlands)"),
                    "KCL": self.mk_user("librarian.kibera", "Kendi Librarian (Kibera)")}
        for u, code, role in ((self.mgr_cbd, "CBD", "branch_manager"), (self.mgr_wlb, "WLB", "branch_manager"),
                              (self.lib["CBD"], "CBD", "librarian"), (self.lib["WLB"], "WLB", "librarian"),
                              (self.lib["KCL"], "KCL", "librarian")):
            BranchRole.objects.create(user=u, branch=self.branches[code], role=role)
        self.m = {
            "amina": self.mk_member("member", "LS-1001", "Amina Member", "Basic", "CBD"),
            "brian": self.mk_member("brian", "LS-1002", "Brian Otieno", "Premium", "CBD"),
            "cara": self.mk_member("cara", "LS-1003", "Cara Wanjiru", "Basic", "WLB", keep_history=True),
            "david": self.mk_member("david", "LS-1004", "David Kimani", "Basic", "CBD"),
            "esther": self.mk_member("esther", "LS-1005", "Esther Njeri", None, "WLB", status="pending"),
            "farouk": self.mk_member("farouk", "LS-1006", "Farouk Hassan", "Basic", "CBD"),
            "grace": self.mk_member("grace", "LS-1007", "Grace Achieng", "Researcher", "KCL"),
            "ian": self.mk_member("ian", "LS-1008", "Ian Mwangi", "Basic", "CBD"),
            "joy": self.mk_member("joy", "LS-1009", "Joy Mutheu", "Basic", "WLB"),
            "kevin": self.mk_member("kevin", "LS-1010", "Kevin Omondi", "Premium", "CBD"),
        }
        self.m["zawadi"] = self.mk_member("zawadi", "LS-1011", "Zawadi Achieng (child)", "Basic", "KCL",
                                          guardian=self.m["grace"], date_of_birth=date(2015, 6, 1))

    # ---- catalog
    def make_catalog(self):
        self.idx, idx = {}, 0
        today = timezone.localdate()
        for title, authors, subject, year, desc in CATALOG:
            idx += 1
            self.idx[title] = idx
            work = Work.objects.create(title=title, description=desc, language="en", first_published=year)
            for a in authors.split("; "):
                creator, _ = Creator.objects.get_or_create(name=a, defaults={"sort_name": a})
                WorkCreator.objects.create(work=work, creator=creator)
            work.subjects.add(Subject.objects.get_or_create(name=subject)[0])
            ed = Edition.objects.create(work=work, resource_type=self.rt["book"], isbn13=isbn13(idx),
                                        publisher="Demo Press", year=year)
            self.add_copies(ed, idx, "", LAYOUT.get(title, DEFAULT_LAYOUTS[idx % 4]), subject, authors, today)
            if title == "Born a Crime":  # a second edition: audiobook CDs
                ad = Edition.objects.create(work=work, resource_type=self.rt["audiobook"], publisher="Demo Audio", year=2017)
                self.add_copies(ad, idx, "A", {"WLB": 1, "CBD": 1}, subject, authors, today)
            work.refresh_search_text()
            Work.objects.filter(pk=work.pk).update(created_at=self.now - timedelta(days=(idx * 3) % 90))
        for title, subject, rtype, volume, issue in PERIODICALS:
            idx += 1
            self.idx[title] = idx
            work = Work.objects.create(title=title, description="Fictional demo periodical.", language="en")
            work.subjects.add(Subject.objects.get_or_create(name=subject)[0])
            ed = Edition.objects.create(work=work, resource_type=self.rt[rtype], publisher="Demo Press",
                                        metadata={"volume": volume, "issue": issue})
            self.add_copies(ed, idx, "", {"CBD": 1, "WLB": 1}, subject, "ZZZ", today)
            work.refresh_search_text()
            Work.objects.filter(pk=work.pk).update(created_at=self.now - timedelta(days=idx % 20))
        # shelf conditions for the staff to see
        for barcode, status in ((f"WLB-{self.idx['Kintu']:03d}-1", "damaged"), (f"WLB-{self.idx['Kintu']:03d}-2", "in_transit"),
                                (f"CBD-{self.idx['Clean Code']:03d}-2", "withdrawn")):
            Item.objects.filter(barcode=barcode).update(status=status)
        Item.objects.filter(barcode=f"CBD-{self.idx['Dust']:03d}-1").update(price=Decimal("900"))

    def add_copies(self, edition, idx, suffix, layout, subject, authors, today):
        last = authors.split("; ")[0].split()[-1][:3].upper()
        for code, n in layout.items():
            for k in range(1, n + 1):
                Item.objects.create(
                    edition=edition, branch=self.branches[code], barcode=f"{code}-{idx:03d}{suffix}-{k}",
                    call_number=f"{subject[:3].upper()} {last}", shelf_location=f"{subject[:3].upper()}-{idx % 9 + 1}",
                    acquired_at=today - timedelta(days=(idx * 3) % 60), price=Decimal(self.rng.choice([650, 800, 1200, 1500])))

    # ------------------------------------------------------------------ helpers for activity
    def pick_barcode(self, title, code):
        item = (Item.objects.filter(edition__work__title=title, edition__resource_type__code="book",
                                    branch__code=code, status="available").order_by("barcode").first())
        if not item:
            raise CommandError(f"seed: no available copy of {title!r} at {code}")
        return item.barcode

    def lend(self, member, title, code=None, due_in=None):
        """Check a title out through the real service. Picks a branch that has a free copy."""
        if code is None:
            for c in [member.home_branch.code] + list(self.branches):
                if Item.objects.filter(edition__work__title=title, edition__resource_type__code="book",
                                       branch__code=c, status="available").exists():
                    code = c
                    break
        loan = services.checkout(member_code=member.member_no, item_barcode=self.pick_barcode(title, code), staff=self.lib[code])
        if due_in is not None:
            self.set_due(loan, due_in)
        return loan

    def give_back(self, loan):
        code = Item.objects.get(pk=loan.item_id).branch.code
        return services.checkin(item_barcode=Item.objects.get(pk=loan.item_id).barcode, staff=self.lib[code])

    def set_due(self, loan, days_from_now):
        due = self.eod(self.now + timedelta(days=days_from_now))
        issued = due - timedelta(days=14)
        Loan.objects.filter(pk=loan.pk).update(issued_at=issued, due_at=due)
        LoanEvent.objects.filter(loan=loan, type="issue").update(at=issued)

    def backdate_returned(self, loan, issued_days_ago, length_days):
        issued = self.now - timedelta(days=issued_days_ago)
        returned = issued + timedelta(days=length_days)
        Loan.objects.filter(pk=loan.pk).update(issued_at=issued, due_at=self.eod(issued + timedelta(days=14)), returned_at=returned)
        LoanEvent.objects.filter(loan=loan, type="issue").update(at=issued)
        LoanEvent.objects.filter(loan=loan, type="return").update(at=returned)

    def work(self, title):
        return Work.objects.get(title=title)

    # ---- two months of ordinary borrowing (feeds "most borrowed", "history", privacy anonymising)
    def random_history(self, n):
        borrowers = [self.m[k] for k in ("amina", "brian", "cara", "grace", "joy", "kevin")]
        works = list(Work.objects.filter(editions__items__isnull=False).distinct().order_by("id"))
        for _ in range(n):
            member, work = self.rng.choice(borrowers), self.rng.choice(works)
            items = list(Item.objects.filter(edition__work=work, status="available").select_related("branch").order_by("barcode"))
            if not items:
                continue
            item = self.rng.choice(items)
            staff = self.lib[item.branch.code]
            loan = services.checkout(member_code=member.member_no, item_barcode=item.barcode, staff=staff)
            services.checkin(item_barcode=item.barcode, staff=staff)
            ago = self.rng.randint(31, 85) if self.rng.random() < 0.55 else self.rng.randint(2, 29)
            self.backdate_returned(loan, ago, self.rng.randint(2, 13))

    # ------------------------------------------------------------------ the story
    def scenarios(self):
        m, hold = self.m, services.place_hold

        # Amina: one book due tomorrow (reminder), one 6 days overdue (fine 60 KES)
        self.set_due(self.lend(m["amina"], "Educated", "CBD"), 1)
        self.set_due(self.lend(m["amina"], "Sapiens", "CBD"), -6)

        # Brian (Premium): one 12 days overdue, two fine
        self.lend(m["brian"], "Deep Work", "CBD", due_in=-12)
        self.lend(m["brian"], "Clean Code", "CBD", due_in=5)
        self.lend(m["brian"], "Cosmos", "CBD", due_in=11)

        # A single-copy bestseller on loan, with a waiting list: Amina first, then Kevin
        self.lend(m["brian"], "Half of a Yellow Sun", "CBD", due_in=9)
        w = self.work("Half of a Yellow Sun")
        h1 = hold(member=m["amina"], work=w, pickup_branch=self.branches["CBD"], actor=m["amina"].user)
        h2 = hold(member=m["kevin"], work=w, pickup_branch=self.branches["CBD"], actor=m["kevin"].user)
        Hold.objects.filter(pk=h1.pk).update(placed_at=self.now - timedelta(days=3))
        Hold.objects.filter(pk=h2.pk).update(placed_at=self.now - timedelta(days=1))

        # Hold becomes READY when the borrower returns the copy
        k1 = self.lend(m["kevin"], "Project Hail Mary", "CBD", due_in=3)
        hold(member=m["brian"], work=self.work("Project Hail Mary"), pickup_branch=self.branches["CBD"], actor=m["brian"].user)
        self.give_back(k1)

        # Expired hold: Cara never collected it
        hc = hold(member=m["cara"], work=self.work("Atomic Habits"), pickup_branch=self.branches["CBD"], actor=m["cara"].user)
        Hold.objects.filter(pk=hc.pk).update(expires_at=self.now - timedelta(hours=2), ready_at=self.now - timedelta(days=4))

        # Cancelled hold: Grace changed her mind
        hg = hold(member=m["grace"], work=self.work("Matilda"), pickup_branch=self.branches["KCL"], actor=m["grace"].user)
        services.cancel_hold(hold_id=hg.id, actor=m["grace"].user)

        # Joy at her loan limit (3): a hold that was fulfilled at the desk, one due TODAY, one next week
        hj = hold(member=m["joy"], work=self.work("The Gruffalo"), pickup_branch=self.branches["WLB"], actor=m["joy"].user)
        loan = services.checkout(member_code=m["joy"].member_no, item_barcode=hj.ready_item.barcode, staff=self.lib["WLB"])
        self.set_due(loan, 6)
        self.lend(m["joy"], "Charlotte's Web", "WLB", due_in=0)
        self.lend(m["joy"], "Matilda", "WLB", due_in=8)

        # Child account linked to a guardian
        self.lend(m["zawadi"], "Where the Wild Things Are", "KCL", due_in=10)

        # Grace: a late return she paid in cash, and one waived by an administrator
        g1 = self.lend(m["grace"], "Guns, Germs, and Steel", "KCL", due_in=-7)
        self.give_back(g1)
        billing.record_payment(member_code=m["grace"].member_no, amount=70, method="cash", reference="RCPT-0457", staff=self.lib["KCL"])
        g2 = self.lend(m["grace"], "Sapiens", "KCL", due_in=-4)
        self.give_back(g2)
        fine = Fine.objects.get(loan=g2, reason="overdue")
        billing.waive_fine(fine_id=fine.id, reason="Library closed for repairs that week", staff=self.admin)
        self.lend(m["grace"], "Braiding Sweetgrass", None, due_in=12)

        # Ian: 80 days late (fine hits the 500 cap) + a 150 damage fee: blocked from borrowing
        i1 = self.lend(m["ian"], "Thinking, Fast and Slow", "CBD", due_in=-80)
        self.give_back(i1)
        Fine.objects.create(member=m["ian"], amount=Decimal("150"), reason="damaged")

        # Kevin: a late return paid part-way by M-Pesa, one book out
        k2 = self.lend(m["kevin"], "Designing Data-Intensive Applications", "CBD", due_in=-22)
        self.give_back(k2)
        billing.record_payment(member_code=m["kevin"].member_no, amount=120, method="mpesa", reference="SBX4T9K1QZ", staff=self.lib["CBD"])
        self.lend(m["kevin"], "The Pragmatic Programmer", "CBD", due_in=3)

        # Farouk: lost a book (fee = the book's price), and his membership has expired
        f1 = self.lend(m["farouk"], "Dust", "CBD", due_in=-30)
        services.mark_lost(loan_id=f1.id, staff=self.lib["CBD"])
        Member.objects.filter(pk=m["farouk"].pk).update(status="expired", expires_at=self.now - timedelta(days=20))

        # David: returned a book, then was suspended
        d1 = self.lend(m["david"], "Matilda", None)
        self.give_back(d1)
        self.backdate_returned(d1, 50, 6)
        Member.objects.filter(pk=m["david"].pk).update(status="suspended")

        # Cara: opted in to keep history, so her old loans stay attached to her name
        for title, ago in (("Things Fall Apart", 120), ("A Grain of Wheat", 95), ("Americanah", 70), ("Unbowed", 52), ("Cosmos", 41)):
            loan = self.lend(m["cara"], title)
            self.give_back(loan)
            self.backdate_returned(loan, ago, 9)

    # ---- notifications, join dates for the growth chart
    def finishing_touches(self):
        for key, m in self.m.items():
            if m.status == "pending":
                continue
            notify(m.user, "membership_approved", member_no=m.member_no)
        Notification.objects.filter(user=self.m["amina"].user, type="membership_approved").update(read_at=self.now)
        for i, key in enumerate(("amina", "brian", "cara", "david", "farouk", "grace", "ian", "joy", "kevin", "zawadi", "esther")):
            Member.objects.filter(pk=self.m[key].pk).update(created_at=self.now - timedelta(days=170 - i * 15))

    # ------------------------------------------------------------------ guide
    def cheat_sheet(self):
        o = self.stdout.write
        o(self.style.SUCCESS("\nDemo library ready. Password for every account: " + PASSWORD))
        o("\nSTAFF LOGINS  (email @" + DOMAIN + ")")
        o("  admin@               super admin: all branches, can delete / clear / waive")
        o("  manager@             manager, City Centre: approves members, waives fines, edits policies")
        o("  manager.westlands@   manager, Westlands")
        o("  librarian@           City Centre librarian: Desk mode works for CBD-… items")
        o("  librarian.westlands@ / librarian.kibera@   other branches (try scanning a CBD item: refused)")
        o("\nMEMBERS  (scan the card number at Staff → Desk)")
        for key, m in self.m.items():
            m.refresh_from_db()
            email = {"amina": "member"}.get(key, key)
            o(f"  {m.member_no}  {email}@  {m.user.full_name:<28} {m.status:<9} {self.story(key)}")
        o("\nBARCODES TO TRY AT THE DESK  (branch-title#-copy; the number is the order in the catalog)")
        for title in ("Half of a Yellow Sun", "Project Hail Mary", "Atomic Habits", "The Lean Startup", "Python Crash Course"):
            it = Item.objects.filter(edition__work__title=title, edition__resource_type__code="book").order_by("barcode").first()
            o(f"  {it.barcode:<12} {title:<28} {it.get_status_display()}")
        o("\nWHAT TO TRY")
        o("  1. Staff → Today: overdue, due-today and ready-for-pickup lists are all populated.")
        o("  2. Desk → scan LS-1008 (Ian) then any book: refused, he owes 650 and the limit is 500.")
        o("  3. Desk → scan LS-1009 (Joy) then any book: refused, loan limit (3) reached.")
        o("  4. Project Hail Mary is on the HOLD SHELF for Brian (LS-1002). At the Desk, scan LS-1002 then its barcode to hand it over.")
        o("  5. As a member, open 'Half of a Yellow Sun': you're on the waitlist; the single copy is out.")
        o("  6. Members → approve Esther (pending); David is suspended; Farouk's membership expired.")
        o("  7. Reports → Overdue, Most borrowed, New arrivals, Membership growth all have data.")
        o("  8. Reserving needs a member login (member@, brian@, cara@ ...). Staff accounts have no member card.")
        o("  9. Policies: Westlands loans 10 days, journals 7 days with no renewals, Premium 21 days. Check due dates.")
        o(" 10. Closures: Dec 12, 25, 26 and Jan 1. A loan whose due date lands on one is pushed to the next open day.")

    @staticmethod
    def story(key):
        return {
            "amina": "due tomorrow + 6 days overdue (owes 60), queue #1 for Half of a Yellow Sun",
            "brian": "Premium; 12 days overdue; hold READY for Project Hail Mary",
            "cara": "keeps history (5 old loans); an expired, uncollected hold",
            "david": "suspended: can't borrow or reserve",
            "esther": "pending approval (no tier yet)",
            "farouk": "expired; lost a book (900 fee + 300 late)",
            "grace": "Researcher; paid fine; waived fine; guardian of Zawadi",
            "ian": "BLOCKED: owes 650 (≥ 500)",
            "joy": "at loan limit; one book due TODAY",
            "kevin": "Premium; part-paid M-Pesa fine (100 left); queue #2",
            "zawadi": "child account, guardian Grace",
        }[key]
