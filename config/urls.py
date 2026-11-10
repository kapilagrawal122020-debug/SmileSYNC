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
router.register("feedback", patients.FeedbackViewSet, "feedback")
router.register("inventory/items", inventory.StockItemViewSet, "stockitem")
router.register("inventory/rules", inventory.UsageRuleViewSet, "usagerule")
router.register("inventory/movements", inventory.StockMovementViewSet, "stockmovement")
router.register("hygiene/checks", hygiene.HygieneCheckViewSet, "hygiene")
router.register("messages", messaging.MessageViewSet, "message")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/login/", accounts.LoginView.as_view()),
    path("api/auth/logout/", accounts.LogoutView.as_view()),
    path("api/auth/me/", accounts.MeView.as_view()),
    path("api/auth/otp/request/", accounts.OtpRequestView.as_view()),
    path("api/auth/otp/verify/", accounts.OtpVerifyView.as_view()),
    path("api/dashboard/", clinic.DashboardView.as_view()),
    path("api/whatsapp/webhook/", messaging.whatsapp_webhook),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
    path("api/", include(router.urls)),
]
