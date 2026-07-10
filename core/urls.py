from django.urls import path

from .views import (
    audit_log_list,
    guide,
    health,
    home,
    project_create,
    project_detail,
    project_edit,
    project_list,
    project_membership_manage,
)


urlpatterns = [
    path("health/", health, name="health"),
    path("", home, name="home"),
    path("audit/", audit_log_list, name="audit_list"),
    path("projects/", project_list, name="project_list"),
    path("projects/new/", project_create, name="project_create"),
    path("projects/<int:pk>/edit/", project_edit, name="project_edit"),
    path("projects/<int:pk>/members/", project_membership_manage, name="project_membership_manage"),
    path("projects/<int:pk>/", project_detail, name="project_detail"),
    path("guide/", guide, name="guide"),
]
