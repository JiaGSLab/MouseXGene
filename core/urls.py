from django.urls import path

from .views import audit_log_list, home


urlpatterns = [
    path("", home, name="home"),
    path("audit/", audit_log_list, name="audit_list"),
]
