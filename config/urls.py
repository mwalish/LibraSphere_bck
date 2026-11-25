from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from accounts import views as accounts
from analytics.views import ReportView
from billing import views as billing
from branches.views import BranchViewSet
from catalog import views as catalog
from circulation import views as circ
from notifications.views import NotificationViewSet

router = DefaultRouter()
router.register("works", catalog.WorkViewSet, basename="work")
router.register("editions", catalog.EditionViewSet, basename="edition")
router.register("items", catalog.ItemViewSet, basename="item")
router.register("resource-types", catalog.ResourceTypeViewSet, basename="resource-type")
router.register("subjects", catalog.SubjectViewSet, basename="subject")
router.register("branches", BranchViewSet, basename="branch")
router.register("members", accounts.MemberViewSet, basename="member")
router.register("loans", circ.LoanViewSet, basename="loan")
router.register("holds", circ.HoldViewSet, basename="hold")
router.register("policies", circ.PolicyViewSet, basename="policy")
router.register("fines", billing.FineViewSet, basename="fine")
router.register("payments", billing.PaymentViewSet, basename="payment")
router.register("notifications", NotificationViewSet, basename="notification")

api = [
    path("auth/register/", accounts.RegisterView.as_view()),
    path("auth/login/", accounts.LoginView.as_view()),
    path("auth/refresh/", accounts.RefreshView.as_view()),
    path("auth/logout/", accounts.LogoutView.as_view()),
    path("me/", accounts.MeView.as_view()),
    path("me/loans/", circ.MyLoansView.as_view()),
    path("me/loans/<int:pk>/renew/", circ.MyRenewView.as_view()),
    path("me/holds/", circ.MyHoldsView.as_view()),
    path("me/fines/", billing.MyFinesView.as_view()),
    path("reports/<slug:kind>/", ReportView.as_view()),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema")),
    path("", include(router.urls)),
]

urlpatterns = [path("admin/", admin.site.urls), path("api/v1/", include(api))]
