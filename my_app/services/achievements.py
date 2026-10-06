"""Server-controlled, non-monetary achievements, progress and levels."""
from django.utils import timezone
from my_app.models import Achievement, CommunityMember, EventParticipant, PlayerRating, TournamentTeam, UserAchievement
from my_app.services.statistics import PlayerStatisticsService
from my_app.services.notifications import NotificationService

LEVELS=((0,1,'New Player'),(5,2,'Active Player'),(20,3,'Regular Player'),(50,4,'Competitive Player'),(100,5,'Elite Player'))
class AchievementProgressService:
 @staticmethod
 def metric(*,user,achievement):
  stats=PlayerStatisticsService.statistics(user=user)
  category=achievement.category
  if category==Achievement.Category.GAMES:return stats['games_completed']
  if category==Achievement.Category.WINS:return stats['games_won']
  if category==Achievement.Category.EVENTS:return stats['events_joined']
  if category==Achievement.Category.COMMUNITIES:return CommunityMember.objects.filter(user=user,status='active').count()
  if category==Achievement.Category.RATINGS:return PlayerRating.objects.filter(player=user,deleted_at__isnull=True).count()
  if category==Achievement.Category.TOURNAMENTS:return TournamentTeam.objects.filter(members__user=user,status='champion').count()
  return 0
class AchievementEvaluationService:
 @classmethod
 def evaluate(cls,*,user):
  result=[]
  for achievement in Achievement.objects.filter(is_active=True):
   progress=AchievementProgressService.metric(user=user,achievement=achievement)
   ua,_=UserAchievement.objects.get_or_create(user=user,achievement=achievement)
   changed=[]
   if ua.progress!=progress:ua.progress=progress;changed.append('progress')
   if not ua.unlocked and progress>=achievement.target_value:
    ua.unlocked=True;ua.unlocked_at=timezone.now();changed+=['unlocked','unlocked_at']
    NotificationService.create_notification(user=user,type='achievement_unlocked',title='Achievement unlocked',body=achievement.name,data={'achievement_id':achievement.public_id.hex},event_key=f'achievement:{achievement.id}')
   if changed:ua.save(update_fields=[*changed,'updated_at'])
   result.append(ua)
  return result
class AchievementService:
 @staticmethod
 def level(*,user):
  activity=PlayerStatisticsService.statistics(user=user)['games_completed']+EventParticipant.objects.filter(user=user,status='registered').count()
  current=LEVELS[0]
  for level in LEVELS:
   if activity>=level[0]:current=level
  return {'level':current[1],'title':current[2],'xp':activity,'next_level_xp':next((item[0] for item in LEVELS if item[0]>activity),None)}
