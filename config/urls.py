from django.contrib import admin
from django.urls import include, path
from rest_framework.routers import DefaultRouter
from audits.views import (
    AuditViewSet,
    EvidenceViewSet,
    FindingViewSet,
    ModuleViewSet,
    EmailTokenObtainPairView,
    LogoutView,
    RefreshCookieView,
    RegisterView,
    health_check,
    shared_audit,
)

router = DefaultRouter()
router.register("audits", AuditViewSet, basename="audit")
router.register("modules", ModuleViewSet, basename="module")
router.register("evidence", EvidenceViewSet, basename="evidence")
router.register("findings", FindingViewSet, basename="finding")

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/token/", EmailTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/auth/register/", RegisterView.as_view(), name="register"),
    path("api/auth/token/refresh/", RefreshCookieView.as_view(), name="token_refresh"),
    path("api/auth/logout/", LogoutView.as_view(), name="logout"),
    path("api/health/", health_check, name="health_check"),
    path("api/shared-audits/<uuid:token>/", shared_audit, name="shared_audit"),
    path("api/", include(router.urls)),
]
