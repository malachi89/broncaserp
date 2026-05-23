def current_business(request):
    return {
        "current_business": getattr(request, "business", None),
        "current_membership": getattr(request, "membership", None),
    }

