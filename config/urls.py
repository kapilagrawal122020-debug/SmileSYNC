"""All API routes live here so the whole surface is visible in one place."""
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework.routers import DefaultRouter

from accounts import views as accounts
from clinic import views as clinic
from hygiene import views as hygiene
from inventory import views as inventory
from messaging import views as messaging
from patients import views as patients
from masterdata import views as masterdata
from platform_admin import views as platform_admin

router = DefaultRouter()
router.register("users", accounts.UserViewSet, "user")
router.register("clinics", clinic.ClinicViewSet, "clinic")
router.register("chairs", clinic.ChairViewSet, "chair")
router.register("documents", clinic.DocumentViewSet, "document")
router.register("patients", patients.PatientViewSet, "patient")
router.register("visits", patients.VisitViewSet, "visit")
router.register("treatments", patients.TreatmentViewSet, "treatment")
router.register("images", patients.PatientImageViewSet, "image")
router.register("invoices", patients.InvoiceViewSet, "invoice")
router.register("payments", patients.PaymentViewSet, "payment")
router.register("advances", patients.AdvanceViewSet, "advance")
router.register("feedback", patients.FeedbackViewSet, "feedback")
router.register("inventory/items", inventory.StockItemViewSet, "stockitem")
router.register("inventory/rules", inventory.UsageRuleViewSet, "usagerule")
router.register("inventory/movements", inventory.StockMovementViewSet, "stockmovement")
router.register("hygiene/checks", hygiene.HygieneCheckViewSet, "hygiene")
router.register("messages", messaging.MessageViewSet, "message")

# Platform admin only (every view uses IsSuperAdmin)
router.register("platform/lists", masterdata.ListViewSet, "platform-list")
router.register("platform/list-values", masterdata.ListValueViewSet, "platform-listvalue")
router.register("platform/clinics", platform_admin.ClinicViewSet, "platform-clinic")
router.register("platform/users", platform_admin.UserViewSet, "platform-user")
router.register("platform/audit", platform_admin.AuditViewSet, "platform-audit")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/login/", accounts.LoginView.as_view()),
    path("api/auth/register/", accounts.RegisterView.as_view()),
    path("api/auth/clinics/", accounts.ClinicChoicesView.as_view()),
    path("api/auth/logout/", accounts.LogoutView.as_view()),
    path("api/auth/me/", accounts.MeView.as_view()),
    path("api/auth/otp/request/", accounts.OtpRequestView.as_view()),
    path("api/auth/otp/verify/", accounts.OtpVerifyView.as_view()),
    path("api/dashboard/", clinic.DashboardView.as_view()),
    path("api/lists/", masterdata.PublicListsView.as_view()),
    path("api/platform/overview/", platform_admin.OverviewView.as_view()),
    path("api/platform/admins/", platform_admin.AdminAccountView.as_view()),
    path("api/whatsapp/webhook/", messaging.whatsapp_webhook),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    path("api/", include(router.urls)),
]
