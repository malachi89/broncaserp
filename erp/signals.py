from django.db.models.signals import post_save
from django.dispatch import receiver

from django.contrib.auth import get_user_model

from .models import CreditAccount, Customer, Location, UserSecurity


User = get_user_model()


@receiver(post_save, sender=Customer)
def ensure_credit_account(sender, instance, created, **kwargs):
    if created:
        CreditAccount.objects.get_or_create(
            business=instance.business,
            customer=instance,
        )


@receiver(post_save, sender=Location)
def keep_one_default_location(sender, instance, **kwargs):
    if instance.is_default:
        Location.objects.filter(business=instance.business, is_default=True).exclude(pk=instance.pk).update(is_default=False)


@receiver(post_save, sender=User)
def ensure_user_security(sender, instance, created, **kwargs):
    UserSecurity.objects.get_or_create(user=instance)
