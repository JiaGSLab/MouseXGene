from django.conf import settings


def app_release(request):
    return {"APP_RELEASE": getattr(settings, "APP_RELEASE", "")}
