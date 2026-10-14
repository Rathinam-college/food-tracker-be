from core.models import Profile
from django.contrib.auth.models import User
users = User.objects.filter(is_superuser=True)
for u in users:
    print(f"User: {u.username}, AutoApprove: {u.profile.auto_approve}")
