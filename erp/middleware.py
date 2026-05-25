from django.shortcuts import redirect

from .models import BusinessMembership


class CurrentTenantMiddleware:
    """Attach the active business to each authenticated non-superuser request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.business = None
        request.membership = None

        if request.user.is_authenticated and not request.user.is_superuser:
            memberships = (
                BusinessMembership.objects.select_related("business")
                .filter(user=request.user, is_active=True)
                .order_by("-created_at", "-id")
            )
            business_id = request.session.get("business_id")
            membership = memberships.filter(business_id=business_id).first()
            if membership is None:
                membership = memberships.first()
                if membership:
                    request.session["business_id"] = membership.business_id

            if membership:
                request.business = membership.business
                request.membership = membership
            must_change_password = getattr(getattr(request.user, "password_security", None), "must_change_password", False)
            if must_change_password and not request.path.startswith(("/logout/", "/admin/", "/cambiar-contrasena/")):
                return redirect("password_change")
            elif not membership and not request.path.startswith(("/logout/", "/admin/", "/cambiar-contrasena/")):
                return redirect("no_business")

        return self.get_response(request)
