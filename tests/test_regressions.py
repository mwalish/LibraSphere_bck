"""Regression tests for bugs found in review, including the two reported on 24 Nov:
approve/reactivate member -> 500 (Celery/Redis import error after the DB commit) and the reserve button -> 409."""
from unittest import mock

from django.contrib import admin
from django.core import mail
from django.test import override_settings

from accounts.models import Member, User
from circulation import services
from circulation.models import Hold, Policy
from notifications.models import Notification
from notifications.tasks import send_notification_email

from .test_librasphere import API, Base


class NotificationRobustnessTests(Base):
    def make_pending(self):
        return Member.objects.create(user=User.objects.create_user("p@t.test", "pw", full_name="P"), home_branch=self.cbd)

    def test_approving_a_member_survives_a_broken_email_queue(self):
        pending = self.make_pending()
        self.as_user(self.mgr_cbd)
        with override_settings(CELERY_TASK_ALWAYS_EAGER=False), \
                mock.patch.object(send_notification_email, "delay", side_effect=ConnectionError("redis is down")), \
                self.captureOnCommitCallbacks(execute=True) as callbacks:
            r = self.client.post(f"{API}/members/{pending.id}/approve/")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(callbacks, "delivery should have been attempted")
        self.assertTrue(Notification.objects.filter(user=pending.user, type="membership_approved").exists())

    def test_eager_mode_sends_inline_and_never_touches_the_broker(self):
        with override_settings(CELERY_TASK_ALWAYS_EAGER=True), \
                mock.patch.object(send_notification_email, "delay", side_effect=AssertionError("must not queue")), \
                self.captureOnCommitCallbacks(execute=True):
            services.place_hold(member=self.member, work=self.work, pickup_branch=self.cbd, actor=self.member.user)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("ready", mail.outbox[0].subject.lower())

    def test_reactivating_a_suspended_member_does_not_resend_the_welcome(self):
        pending = self.make_pending()
        self.as_user(self.mgr_cbd)
        self.assertEqual(self.client.post(f"{API}/members/{pending.id}/approve/").status_code, 200)
        self.assertEqual(self.client.post(f"{API}/members/{pending.id}/suspend/").data["status"], "suspended")
        self.assertEqual(self.client.post(f"{API}/members/{pending.id}/approve/").data["status"], "active")
        self.assertEqual(Notification.objects.filter(user=pending.user, type="membership_approved").count(), 1)


class ReserveFlowTests(Base):
    def reserve(self, user):
        self.as_user(user)
        return self.client.post(f"{API}/holds/", {"work": self.work.id, "pickup_branch": self.cbd.id}, format="json")

    def test_second_click_is_a_clear_409_and_cancel_allows_trying_again(self):
        first = self.reserve(self.member.user)
        self.assertEqual((first.status_code, first.data["status"]), (201, "ready"))
        again = self.reserve(self.member.user)
        self.assertEqual((again.status_code, again.data["code"]), (409, "duplicate_hold"))
        self.assertEqual(self.client.get(f"{API}/me/holds/").data[0]["id"], first.data["id"])  # the UI can see it
        self.client.delete(f"{API}/holds/{first.data['id']}/")
        self.assertEqual(self.reserve(self.member.user).status_code, 201)

    def test_suspended_and_pending_members_get_a_readable_refusal(self):
        for status in ("suspended", "pending", "expired"):
            Member.objects.filter(pk=self.member.pk).update(status=status)
            r = self.reserve(self.member.user)
            self.assertEqual((r.status_code, r.data["code"]), (409, "member_inactive"), status)
            self.assertIn("detail", r.data)

    def test_member_on_loan_cannot_reserve_the_same_title(self):
        services.checkout(member_code=self.member.member_no, item_barcode=self.item.barcode, staff=self.lib_cbd)
        self.assertEqual(self.reserve(self.member.user).data["code"], "already_borrowed")

    def test_staff_accounts_without_a_member_card_cannot_reserve(self):
        self.assertEqual(self.reserve(self.lib_cbd).status_code, 403)


class AdminAndApiHygieneTests(Base):
    APPS = {"accounts", "branches", "catalog", "circulation", "billing", "core"}

    def test_admin_changelists_and_add_pages_open(self):
        self.client.force_login(self.admin)
        for model in admin.site._registry:
            if model._meta.app_label not in self.APPS:
                continue
            base = f"/admin/{model._meta.app_label}/{model._meta.model_name}/"
            for url in (base, base + "add/"):
                self.assertEqual(self.client.get(url).status_code, 200, url)

    def test_admin_can_create_an_email_user(self):
        self.client.force_login(self.admin)
        r = self.client.post("/admin/accounts/user/add/", {"email": "new@t.test", "full_name": "New",
                                                           "password1": "Str0ng-pass-99", "password2": "Str0ng-pass-99"})
        self.assertEqual(r.status_code, 302, getattr(r, "context", None) and r.context["adminform"].form.errors)
        self.assertTrue(User.objects.get(email="new@t.test").check_password("Str0ng-pass-99"))

    def test_edition_endpoint_requires_a_work(self):
        self.as_user(self.lib_cbd)
        r = self.client.post(f"{API}/editions/", {"resource_type": "book"}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("work", r.data)

    def test_report_parameters_are_validated_not_500(self):
        self.as_user(self.lib_cbd)
        self.assertEqual(self.client.get(f"{API}/reports/most-borrowed/?from=banana").status_code, 400)
        self.assertEqual(self.client.get(f"{API}/reports/new-arrivals/?days=abc").status_code, 400)
        self.assertEqual(self.client.get(f"{API}/reports/most-borrowed/?from=2020-01-01&to=2099-01-01").status_code, 200)
        self.assertEqual(self.client.get(f"{API}/reports/nope/").status_code, 404)

    def test_membership_growth_groups_by_month_in_python(self):
        self.as_user(self.admin)
        rows = self.client.get(f"{API}/reports/membership-growth/").data
        self.assertEqual(sum(r["new_members"] for r in rows), Member.objects.count())
        self.assertTrue(all(r["month"].endswith("-01") for r in rows))

    def test_scanning_unknown_cards_and_items_is_a_clean_404(self):
        self.as_user(self.lib_cbd)
        self.assertEqual(self.client.get(f"{API}/members/lookup/?code=LS-NOPE").status_code, 404)
        self.assertEqual(self.client.post(f"{API}/loans/checkout/", {"member": "LS-NOPE", "item": self.item.barcode}, format="json").status_code, 404)
        self.assertEqual(self.client.post(f"{API}/loans/checkin/", {"item": "NOPE-1"}, format="json").status_code, 404)

    def test_renewal_is_refused_for_a_suspended_member(self):
        loan = services.checkout(member_code=self.member.member_no, item_barcode=self.item.barcode, staff=self.lib_cbd)
        Member.objects.filter(pk=self.member.pk).update(status="suspended")
        self.as_user(self.member.user)
        self.assertEqual(self.client.post(f"{API}/me/loans/{loan.id}/renew/").data["code"], "member_inactive")

    def test_duplicate_policy_rows_resolve_deterministically(self):
        first = Policy.objects.create(loan_days=9)
        Policy.objects.create(loan_days=30)
        self.assertEqual(services.resolve_policy(self.cbd, self.tier, self.book).loan_days, first.loan_days)

    def test_hold_queue_positions_are_reported(self):
        services.checkout(member_code=self.member.member_no, item_barcode=self.item.barcode, staff=self.lib_cbd)
        services.place_hold(member=self.member2, work=self.work, pickup_branch=self.cbd, actor=self.member2.user)
        self.as_user(self.member2.user)
        h = self.client.get(f"{API}/me/holds/").data[0]
        self.assertEqual((h["status"], h["position"]), ("queued", 1))
        self.assertEqual(Hold.objects.count(), 1)
