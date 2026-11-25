"""Runs the demo seeder (real services end to end) and then walks the whole API as every role."""
from io import StringIO

from django.core.management import call_command
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Member, User
from billing.models import Fine
from branches.models import Branch
from catalog.models import Item, Work
from circulation.management.commands.seed_demo import isbn13
from circulation.models import Hold, Loan
from notifications.models import Notification

API = "/api/v1"


class SeedAndSmokeTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        stream = StringIO()
        call_command("seed_demo", force=True, reset=True, stdout=stream)
        cls.printed = stream.getvalue()   # plain string: setUpTestData deep-copies attributes, StringIO can't be copied

    def user(self, key):
        return User.objects.get(email=f"{key}@librasphere.test")

    @staticmethod
    def now():
        return timezone.now()

    # ---------------------------------------------------------------- the data tells the story
    def test_catalog_is_rich(self):
        self.assertGreaterEqual(Work.objects.count(), 40)
        self.assertEqual(Branch.objects.count(), 3)
        self.assertEqual(Work.objects.get(title="Born a Crime").editions.count(), 2)  # book + audiobook
        self.assertEqual(len({isbn13(i) for i in range(1, 60)}), 59)

    def test_every_state_is_represented(self):
        have = lambda qs, field: set(qs.values_list(field, flat=True))  # noqa: E731
        self.assertGreaterEqual(have(Member.objects.all(), "status"), {"active", "pending", "suspended", "expired"})
        self.assertGreaterEqual(have(Hold.objects.all(), "status"), {"queued", "ready", "expired", "cancelled", "fulfilled"})
        self.assertGreaterEqual(have(Fine.objects.all(), "status"), {"open", "paid", "waived"})
        self.assertGreaterEqual(have(Fine.objects.all(), "reason"), {"overdue", "lost", "damaged"})
        self.assertGreaterEqual(have(Item.objects.all(), "status"),
                                {"available", "on_loan", "hold_shelf", "lost", "damaged", "in_transit", "withdrawn"})
        self.assertGreaterEqual(Loan.objects.filter(returned_at__isnull=True, due_at__lt=self.now()).count(), 2)
        self.assertTrue(Loan.objects.filter(member__isnull=True).exists(), "old history should be anonymised")
        self.assertTrue(Loan.objects.filter(member__member_no="LS-1003", returned_at__isnull=False).exists(), "Cara keeps history")
        self.assertGreater(Notification.objects.count(), 5)

    def test_demo_members_hit_the_business_rules(self):
        lib_wlb, lib_cbd = self.user("librarian.westlands"), self.user("librarian")
        self.client.force_authenticate(lib_cbd)
        free_cbd = Item.objects.filter(branch__code="CBD", status="available").first().barcode
        codes = {}
        for member_no in ("LS-1008", "LS-1004", "LS-1005", "LS-1006"):   # Ian, David, Esther, Farouk
            r = self.client.post(f"{API}/loans/checkout/", {"member": member_no, "item": free_cbd}, format="json")
            codes[member_no] = (r.status_code, r.data["code"])
        self.assertEqual(codes["LS-1008"], (409, "fines_block"))
        for member_no in ("LS-1004", "LS-1005", "LS-1006"):
            self.assertEqual(codes[member_no], (409, "member_inactive"), member_no)
        self.client.force_authenticate(lib_wlb)  # Joy is at her limit
        free_wlb = Item.objects.filter(branch__code="WLB", status="available").first().barcode
        r = self.client.post(f"{API}/loans/checkout/", {"member": "LS-1009", "item": free_wlb}, format="json")
        self.assertEqual((r.status_code, r.data["code"]), (409, "loan_limit"))
        # a Westlands librarian can't lend a City Centre copy
        r = self.client.post(f"{API}/loans/checkout/", {"member": "LS-1001", "item": free_cbd}, format="json")
        self.assertEqual(r.status_code, 403)

    def test_hold_stories(self):
        brian = Hold.objects.get(member__member_no="LS-1002", work__title="Project Hail Mary")
        self.assertEqual(brian.status, "ready")
        self.assertEqual(brian.ready_item.status, "hold_shelf")
        self.client.force_authenticate(self.user("member"))   # Amina
        mine = {h["title"]: h for h in self.client.get(f"{API}/me/holds/").data}
        self.assertEqual(mine["Half of a Yellow Sun"]["position"], 1)
        self.client.force_authenticate(self.user("kevin"))
        self.assertEqual({h["title"]: h for h in self.client.get(f"{API}/me/holds/").data}["Half of a Yellow Sun"]["position"], 2)

    def test_fines_add_up(self):
        owed = lambda no: sum(f.balance for f in Fine.objects.filter(member__member_no=no, status="open"))  # noqa: E731
        self.assertEqual(owed("LS-1008"), 650)        # Ian: 500 (cap) + 150
        self.assertEqual(owed("LS-1010"), 100)        # Kevin: 220 - 120 paid by M-Pesa
        self.assertEqual(owed("LS-1007"), 0)          # Grace: one paid, one waived
        self.assertEqual(owed("LS-1001"), 60)         # Amina: 6 days late
        self.assertEqual(owed("LS-1002"), 120)        # Brian: 12 days late

    def test_seeding_again_without_reset_changes_nothing(self):
        before = (Work.objects.count(), Member.objects.count(), Loan.objects.count())
        out = StringIO()
        call_command("seed_demo", force=True, stdout=out)
        self.assertIn("already exists", out.getvalue())
        self.assertEqual(before, (Work.objects.count(), Member.objects.count(), Loan.objects.count()))

    def test_cheat_sheet_was_printed(self):
        text = self.printed
        for needle in ("LS-1008", "librarian@", "WHAT TO TRY", "CBD-"):
            self.assertIn(needle, text)

    # ---------------------------------------------------------------- nothing returns a 500
    def test_every_read_endpoint_for_admin(self):
        self.client.force_authenticate(self.user("admin"))
        work, cbd = Work.objects.get(title="Sapiens"), Branch.objects.get(code="CBD")
        urls = ["/works/", "/works/?q=sapiens", "/works/?q=achebe", f"/works/?available=1&branch={cbd.id}",
                "/works/?resource_type=journal", "/works/?ordering=-created_at&page_size=5", "/works/popular/",
                f"/works/{work.id}/", f"/works/{work.id}/availability/", "/branches/", "/resource-types/", "/subjects/",
                "/members/", "/members/?status=pending&search=est", "/members/lookup/?code=LS-1001", "/loans/",
                "/loans/?status=overdue", "/loans/?status=active", "/loans/?status=returned", "/loans/?member=LS-1001",
                "/holds/", "/holds/?status=ready", "/fines/", "/fines/?status=open", "/payments/", "/policies/",
                "/notifications/", "/items/", "/items/?status=lost", "/reports/today/", "/reports/overdue/",
                "/reports/most-borrowed/", "/reports/new-arrivals/", "/reports/membership-growth/", "/me/", "/schema/"]
        for url in urls:
            r = self.client.get(API + url)
            self.assertEqual(r.status_code, 200, f"{url} -> {r.status_code}")

    def test_search_finds_things(self):
        for q, expect in (("sapiens", "Sapiens"), ("achebe", "Things Fall Apart"), ("harari", "Sapiens"), ("python", "Python Crash Course")):
            titles = [w["title"] for w in self.client.get(f"{API}/works/?q={q}").data["results"]]
            self.assertIn(expect, titles, q)
        self.assertEqual(self.client.get(f"{API}/works/?q=zzzznotabook").data["count"], 0)

    def test_every_endpoint_for_a_member(self):
        self.client.force_authenticate(self.user("member"))
        for url in ("/me/", "/me/loans/", "/me/loans/?history=1", "/me/holds/", "/me/fines/", "/notifications/", "/works/", "/branches/"):
            self.assertEqual(self.client.get(API + url).status_code, 200, url)
        for url in ("/loans/", "/members/", "/fines/", "/payments/", "/reports/today/", "/policies/", "/items/"):
            self.assertEqual(self.client.get(API + url).status_code, 403, url)

    def test_member_can_renew_and_see_the_effect(self):
        self.client.force_authenticate(self.user("brian"))      # Cosmos is not on hold: renewable
        loan = next(l for l in self.client.get(f"{API}/me/loans/").data if l["title"] == "Cosmos")
        r = self.client.post(f"{API}/me/loans/{loan['id']}/renew/")
        self.assertEqual(r.status_code, 200)
        self.assertGreater(r.data["due_at"], loan["due_at"])
        self.assertEqual(r.data["renewals_count"], 1)

    def test_an_item_on_the_hold_shelf_goes_only_to_its_holder(self):
        hold = Hold.objects.get(member__member_no="LS-1002", work__title="Project Hail Mary")
        self.client.force_authenticate(self.user("librarian"))
        wrong = self.client.post(f"{API}/loans/checkout/", {"member": "LS-1001", "item": hold.ready_item.barcode}, format="json")
        self.assertEqual(wrong.data["code"], "reserved_for_other")
        right = self.client.post(f"{API}/loans/checkout/", {"member": "LS-1002", "item": hold.ready_item.barcode}, format="json")
        self.assertEqual(right.status_code, 201)
        hold.refresh_from_db()
        self.assertEqual(hold.status, "fulfilled")
