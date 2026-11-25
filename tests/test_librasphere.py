"""Run with: python manage.py test"""
from datetime import timedelta
from decimal import Decimal

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import BranchRole, Member, MembershipTier, User
from billing.models import Fine
from branches.models import Branch, Library
from catalog.models import Edition, Item, ResourceType, Work
from circulation import services
from circulation.models import Hold, Loan, Policy

API = "/api/v1"


class Base(APITestCase):
    @classmethod
    def setUpTestData(cls):
        lib = Library.objects.create(name="Test")
        cls.cbd = Branch.objects.create(library=lib, name="City", code="CBD")
        cls.wlb = Branch.objects.create(library=lib, name="West", code="WLB")
        cls.book = ResourceType.objects.create(code="book", name="Book")
        cls.journal = ResourceType.objects.create(code="journal", name="Journal",
                                                  metadata_template={"required": ["volume"]})
        cls.tier = MembershipTier.objects.create(name="Basic", loan_limit=2, hold_limit=2)
        cls.admin = User.objects.create_superuser("admin@t.test", "pw")
        cls.lib_cbd = User.objects.create_user("lib@t.test", "pw", full_name="Leo")
        BranchRole.objects.create(user=cls.lib_cbd, branch=cls.cbd, role="librarian")
        cls.mgr_cbd = User.objects.create_user("mgr@t.test", "pw")
        BranchRole.objects.create(user=cls.mgr_cbd, branch=cls.cbd, role="branch_manager")
        cls.work = Work.objects.create(title="Sapiens")
        cls.edition = Edition.objects.create(work=cls.work, resource_type=cls.book)
        cls.item = Item.objects.create(edition=cls.edition, branch=cls.cbd)
        cls.member = cls.make_member("m1@t.test", "Amina")
        cls.member2 = cls.make_member("m2@t.test", "Brian")

    @classmethod
    def make_member(cls, email, name):
        u = User.objects.create_user(email, "pw", full_name=name)
        return Member.objects.create(user=u, home_branch=cls.cbd, tier=cls.tier, status="active",
                                     expires_at=timezone.now() + timedelta(days=30))

    def as_user(self, user):
        self.client.force_authenticate(user)


class CatalogTests(Base):
    def test_public_catalog_needs_no_login_and_shows_availability(self):
        r = self.client.get(f"{API}/works/?q=sapi")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["results"][0]["available_copies"], 1)

    def test_anonymous_cannot_write(self):
        self.assertEqual(self.client.post(f"{API}/works/", {"title": "X"}, format="json").status_code, 401)

    def test_staff_creates_work_with_copies_and_duplicate_isbn_rejected(self):
        self.as_user(self.lib_cbd)
        body = {"title": "Educated", "creators": ["Tara Westover"], "subjects": ["Memoir"],
                "editions": [{"resource_type": "book", "isbn13": "978-1-2345-6789-7",
                              "copies": [{"branch": self.cbd.id, "count": 2}]}]}
        r = self.client.post(f"{API}/works/", body, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Item.objects.filter(edition__work__title="Educated").count(), 2)
        self.assertEqual(self.client.get(f"{API}/works/?q=westover").data["count"], 1)  # creator is searchable
        dup = self.client.post(f"{API}/works/", {**body, "title": "Educated 2"}, format="json")
        self.assertEqual(dup.status_code, 400)

    def test_librarian_cannot_add_copies_at_other_branch(self):
        self.as_user(self.lib_cbd)
        body = {"title": "Y", "editions": [{"resource_type": "book", "copies": [{"branch": self.wlb.id}]}]}
        self.assertEqual(self.client.post(f"{API}/works/", body, format="json").status_code, 400)

    def test_metadata_template_enforced(self):
        self.as_user(self.lib_cbd)
        body = {"title": "J", "editions": [{"resource_type": "journal", "metadata": {}}]}
        self.assertEqual(self.client.post(f"{API}/works/", body, format="json").status_code, 400)


class CirculationTests(Base):
    def checkout(self, member=None, item=None, user=None):
        self.as_user(user or self.lib_cbd)
        return self.client.post(f"{API}/loans/checkout/", {"member": (member or self.member).member_no,
                                                             "item": (item or self.item).barcode}, format="json")

    def test_checkout_and_checkin_roundtrip(self):
        r = self.checkout()
        self.assertEqual(r.status_code, 201, r.data)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "on_loan")
        self.assertEqual(self.checkout(self.member2).status_code, 409)  # already on loan
        r = self.client.post(f"{API}/loans/checkin/", {"item": self.item.barcode}, format="json")
        self.assertEqual(r.data["action"], "shelve")
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "available")

    def test_loan_limit(self):
        for i in range(2):
            it = Item.objects.create(edition=self.edition, branch=self.cbd)
            self.assertEqual(self.checkout(item=it).status_code, 201)
        r = self.checkout()
        self.assertEqual((r.status_code, r.data["code"]), (409, "loan_limit"))

    def test_librarian_cannot_lend_from_other_branch(self):
        far = Item.objects.create(edition=self.edition, branch=self.wlb)
        self.assertEqual(self.checkout(item=far).status_code, 403)

    def test_pending_member_cannot_borrow(self):
        self.member.status = "pending"
        self.member.save()
        self.assertEqual(self.checkout().data["code"], "member_inactive")

    def test_fines_block_borrowing_and_payment_clears_it(self):
        Fine.objects.create(member=self.member, amount=Decimal("600"), reason="other")
        self.assertEqual(self.checkout().data["code"], "fines_block")
        self.as_user(self.lib_cbd)
        r = self.client.post(f"{API}/payments/", {"member": self.member.member_no, "amount": "200"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(self.checkout().status_code, 201)  # owes 400 < threshold 500

    def test_overdue_fine_is_assessed_on_return_and_capped(self):
        loan = services.checkout(member_code=self.member.member_no, item_barcode=self.item.barcode, staff=self.lib_cbd)
        Loan.objects.filter(pk=loan.pk).update(due_at=timezone.now() - timedelta(days=100))
        self.as_user(self.lib_cbd)
        r = self.client.post(f"{API}/loans/checkin/", {"item": self.item.barcode}, format="json")
        self.assertEqual(r.data["fine"], "500.00")  # 100 days * 10 would be 1000; capped at 500

    def test_renew_rules(self):
        loan = services.checkout(member_code=self.member.member_no, item_barcode=self.item.barcode, staff=self.lib_cbd)
        self.as_user(self.member.user)
        for _ in range(2):
            self.assertEqual(self.client.post(f"{API}/me/loans/{loan.id}/renew/").status_code, 200)
        r = self.client.post(f"{API}/me/loans/{loan.id}/renew/")
        self.assertEqual(r.data["code"], "max_renewals")
        self.as_user(self.member2.user)  # someone else's loan
        self.assertEqual(self.client.post(f"{API}/me/loans/{loan.id}/renew/").status_code, 403)

    def test_closure_days_push_due_date(self):
        due = services.compute_due(self.cbd, 14)
        self.cbd.closures.create(date=due.date())
        self.assertEqual(services.compute_due(self.cbd, 14).date(), due.date() + timedelta(days=1))

    def test_policy_specificity(self):
        Policy.objects.create(branch=None, tier=None, resource_type=None, loan_days=14)
        Policy.objects.create(branch=self.cbd, tier=None, resource_type=None, loan_days=7)
        Policy.objects.create(branch=self.cbd, tier=self.tier, resource_type=self.journal, loan_days=3)
        self.assertEqual(services.resolve_policy(self.cbd, self.tier, self.book).loan_days, 7)
        self.assertEqual(services.resolve_policy(self.cbd, self.tier, self.journal).loan_days, 3)
        self.assertEqual(services.resolve_policy(self.wlb, self.tier, self.book).loan_days, 14)


class HoldTests(Base):
    def test_immediate_hold_when_copy_on_shelf_then_checkout_by_holder_only(self):
        self.as_user(self.member.user)
        r = self.client.post(f"{API}/holds/", {"work": self.work.id, "pickup_branch": self.cbd.id}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(r.data["status"], "ready")
        self.as_user(self.lib_cbd)
        other = self.client.post(f"{API}/loans/checkout/", {"member": self.member2.member_no,
                                                               "item": self.item.barcode}, format="json")
        self.assertEqual(other.data["code"], "reserved_for_other")
        mine = self.client.post(f"{API}/loans/checkout/", {"member": self.member.member_no,
                                                              "item": self.item.barcode}, format="json")
        self.assertEqual(mine.status_code, 201)
        self.assertEqual(Hold.objects.get().status, "fulfilled")

    def test_queue_order_and_return_goes_to_next_in_line(self):
        services.checkout(member_code=self.member.member_no, item_barcode=self.item.barcode, staff=self.lib_cbd)
        m3 = self.make_member("m3@t.test", "Cara")
        services.place_hold(member=self.member2, work=self.work, pickup_branch=self.cbd, actor=self.member2.user)
        services.place_hold(member=m3, work=self.work, pickup_branch=self.cbd, actor=m3.user)
        self.as_user(self.lib_cbd)
        r = self.client.post(f"{API}/loans/checkin/", {"item": self.item.barcode}, format="json")
        self.assertEqual(r.data["action"], "hold")
        self.assertEqual(r.data["hold"]["member_no"], self.member2.member_no)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "hold_shelf")
        self.assertEqual(Hold.objects.get(member=m3).status, "queued")

    def test_hold_blocks_renewal(self):
        loan = services.checkout(member_code=self.member.member_no, item_barcode=self.item.barcode, staff=self.lib_cbd)
        services.place_hold(member=self.member2, work=self.work, pickup_branch=self.cbd, actor=self.member2.user)
        self.as_user(self.member.user)
        self.assertEqual(self.client.post(f"{API}/me/loans/{loan.id}/renew/").data["code"], "held_by_others")

    def test_duplicate_hold_and_no_copies_at_branch(self):
        services.place_hold(member=self.member, work=self.work, pickup_branch=self.cbd, actor=self.member.user)
        with self.assertRaises(Exception) as ctx:
            services.place_hold(member=self.member, work=self.work, pickup_branch=self.cbd, actor=self.member.user)
        self.assertEqual(ctx.exception.detail["code"], "duplicate_hold")
        with self.assertRaises(Exception) as ctx:
            services.place_hold(member=self.member2, work=self.work, pickup_branch=self.wlb, actor=self.member2.user)
        self.assertEqual(ctx.exception.detail["code"], "no_copies_at_branch")

    def test_expired_hold_releases_copy_to_next_member(self):
        services.place_hold(member=self.member, work=self.work, pickup_branch=self.cbd, actor=self.member.user)
        services.place_hold(member=self.member2, work=self.work, pickup_branch=self.cbd, actor=self.member2.user)
        Hold.objects.filter(member=self.member).update(expires_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(services.expire_holds(), 1)
        self.assertEqual(Hold.objects.get(member=self.member).status, "expired")
        self.assertEqual(Hold.objects.get(member=self.member2).status, "ready")

    def test_cancel_ready_hold_frees_copy(self):
        h = services.place_hold(member=self.member, work=self.work, pickup_branch=self.cbd, actor=self.member.user)
        self.as_user(self.member.user)
        self.assertEqual(self.client.delete(f"{API}/holds/{h.id}/").status_code, 200)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, "available")


class PrivacyAndRolesTests(Base):
    def test_history_anonymised_unless_member_keeps_it(self):
        keeper = self.make_member("keep@t.test", "Keeper")
        keeper.keep_history = True
        keeper.save()
        old = timezone.now() - timedelta(days=90)
        for m in (self.member, keeper):
            it = Item.objects.create(edition=self.edition, branch=self.cbd)
            loan = services.checkout(member_code=m.member_no, item_barcode=it.barcode, staff=self.lib_cbd)
            services.checkin(item_barcode=it.barcode, staff=self.lib_cbd)
            Loan.objects.filter(pk=loan.pk).update(returned_at=old)
        self.assertEqual(services.anonymize_history(), 1)
        self.assertEqual(Loan.objects.filter(member__isnull=True).count(), 1)

    def test_members_cannot_use_staff_endpoints(self):
        self.as_user(self.member.user)
        for url in ("/loans/", "/members/", "/fines/", "/reports/overdue/"):
            self.assertEqual(self.client.get(API + url).status_code, 403, url)

    def test_branch_scoping_of_staff_lists(self):
        wlb_member = Member.objects.create(user=User.objects.create_user("w@t.test", "pw", full_name="W"),
                                           home_branch=self.wlb, status="active")
        self.as_user(self.lib_cbd)
        nos = {m["member_no"] for m in self.client.get(f"{API}/members/").data["results"]}
        self.assertIn(self.member.member_no, nos)
        self.assertNotIn(wlb_member.member_no, nos)
        self.as_user(self.admin)
        self.assertEqual(self.client.get(f"{API}/members/").data["count"], 3)

    def test_only_manager_approves_and_waives(self):
        pending = Member.objects.create(user=User.objects.create_user("p@t.test", "pw", full_name="P"),
                                        home_branch=self.cbd)
        self.as_user(self.lib_cbd)
        self.assertEqual(self.client.post(f"{API}/members/{pending.id}/approve/").status_code, 403)
        self.as_user(self.mgr_cbd)
        self.assertEqual(self.client.post(f"{API}/members/{pending.id}/approve/").data["status"], "active")

    def test_registration_creates_pending_member(self):
        self.client.force_authenticate(None)
        r = self.client.post(f"{API}/auth/register/", {"email": "new@t.test", "password": "Str0ng-pass-99",
                                                         "full_name": "New", "home_branch": self.cbd.id}, format="json")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual(Member.objects.get(user__email="new@t.test").status, "pending")

    def test_jwt_login_with_email(self):
        self.client.force_authenticate(None)
        r = self.client.post(f"{API}/auth/login/", {"email": "lib@t.test", "password": "pw"}, format="json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("access", r.data)

    def test_reports(self):
        services.checkout(member_code=self.member.member_no, item_barcode=self.item.barcode, staff=self.lib_cbd)
        self.as_user(self.lib_cbd)
        self.assertEqual(self.client.get(f"{API}/reports/today/").data["active_loans"], 1)
        self.assertEqual(self.client.get(f"{API}/reports/most-borrowed/").data[0]["title"], "Sapiens")
