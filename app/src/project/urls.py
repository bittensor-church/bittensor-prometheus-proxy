from django.conf import settings
from django.contrib.admin.sites import site
from django.urls import include, path

from .core.business_metrics import metrics_manager
from .core.metrics import metrics_view

urlpatterns = [
    path("admin/", site.urls),
    path("metrics", metrics_view, name="prometheus-django-metrics"),
    path("business-metrics", metrics_manager.view, name="prometheus-business-metrics"),
    path("healthcheck/", include("health_check.urls")),
    path("prometheus/", include("project.core.views.prometheus")),
    path("sentry/", include("project.core.views.sentry")),
    path("traces/", include("project.core.views.traces")),
    path("", include("django.contrib.auth.urls")),
]

if settings.DEBUG_TOOLBAR:
    urlpatterns += [
        path("__debug__/", include("debug_toolbar.urls")),
    ]
