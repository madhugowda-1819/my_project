"""Central in-app notification service; push delivery remains provider-neutral."""
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from my_app.models import Notification, NotificationPreference, UserDevice

TYPE_CATEGORY={'message':'chat','game_joined':'game','game_cancelled':'game','booking_created':'booking','booking_cancelled':'booking','community_join_request':'community','community_joined':'community','event_registered':'event','event_waitlisted':'event','event_waitlist_promoted':'event','tournament_match_result':'tournament'}
class NotificationService:
 @staticmethod
 def _allowed(user,notification_type):
  pref,_=NotificationPreference.objects.get_or_create(user=user); category=TYPE_CATEGORY.get(notification_type)
  return not category or getattr(pref,f'{category}_notifications',True)
 @classmethod
 def create_notification(cls,*,user,type,title,body='',data=None,priority=1,actor=None,event_key=''):
  if not user.is_active or not cls._allowed(user,type): return None
  if event_key:
   existing=Notification.objects.filter(user=user,event_key=event_key,deleted_at__isnull=True).first()
   if existing:return existing
  return Notification.objects.create(user=user,actor=actor,type=type,title=title[:200],body=body,data=data or {},priority=priority,event_key=event_key)
 @classmethod
 def create_bulk_notifications(cls,*,users,**kwargs): return [n for n in (cls.create_notification(user=u,**kwargs) for u in users) if n]
 @staticmethod
 def mark_as_read(*,notification,user):
  if notification.user_id!=user.id: raise PermissionDenied('You cannot modify this notification.')
  if not notification.is_read: notification.is_read=True;notification.read_at=timezone.now();notification.save(update_fields=['is_read','read_at','updated_at'])
  return notification
 @staticmethod
 def mark_all_as_read(*,user): return Notification.objects.filter(user=user,is_read=False,deleted_at__isnull=True).update(is_read=True,read_at=timezone.now(),updated_at=timezone.now())
 @staticmethod
 def delete_notification(*,notification,user):
  if notification.user_id!=user.id: raise PermissionDenied('You cannot delete this notification.')
  notification.deleted_at=timezone.now();notification.save(update_fields=['deleted_at','updated_at']);return notification
 @staticmethod
 def unread_count(*,user): return Notification.objects.filter(user=user,is_read=False,deleted_at__isnull=True).count()
class DeviceService:
 @staticmethod
 @transaction.atomic
 def register(*,user,device_token,platform,device_name=''):
  if not device_token.strip(): raise ValidationError({'device_token':['Required.']})
  device,created=UserDevice.objects.update_or_create(device_token=device_token.strip(),defaults={'user':user,'platform':platform,'device_name':device_name.strip(),'is_active':True})
  return device,created
