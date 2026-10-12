from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q
from decimal import Decimal
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from my_app.models import EventParticipant, Game, GamePlayer, PlayerRating, PlayerSportStatistics

class RatingService:
 @classmethod
 def rate_player(cls,*,reviewer,player,game,playing_performance,teamwork,sportsmanship,punctuality,review=''):
  if reviewer.id==player.id: raise ValidationError('You cannot rate yourself.')
  if not player.is_active: raise ValidationError('Player is inactive.')
  if game.status!=Game.Status.COMPLETED or not GamePlayer.objects.filter(game=game,user=reviewer,status='confirmed').exists() or not GamePlayer.objects.filter(game=game,user=player,status='confirmed').exists(): raise PermissionDenied('A completed shared game is required.')
  if len(review)>2000: raise ValidationError({'review':['Review cannot exceed 2000 characters.']})
  overall=Decimal(playing_performance+teamwork+sportsmanship+punctuality)/Decimal('4')
  try:
   with transaction.atomic():
    return PlayerRating.objects.create(reviewer=reviewer,player=player,game=game,rating=overall,playing_performance=playing_performance,teamwork=teamwork,sportsmanship=sportsmanship,punctuality=punctuality,review=review.strip())
  except IntegrityError as exc: raise ValidationError({'game_id':['You have already rated this player for this game.']}) from exc
 @staticmethod
 def aggregate(player):
  q=PlayerRating.objects.filter(player=player,deleted_at__isnull=True);a=q.aggregate(avg=Avg('rating'),count=Count('id'),playing_performance=Avg('playing_performance'),teamwork=Avg('teamwork'),sportsmanship=Avg('sportsmanship'),punctuality=Avg('punctuality'));return {'average_rating':round(float(a['avg'] or 0),2),'rating_count':a['count'],'playing_performance':round(float(a['playing_performance'] or 0),2),'teamwork':round(float(a['teamwork'] or 0),2),'sportsmanship':round(float(a['sportsmanship'] or 0),2),'punctuality':round(float(a['punctuality'] or 0),2),'distribution':{str(i):q.filter(rating=i).count() for i in range(1,6)}}
 @staticmethod
 def eligible_games(*, reviewer, player):
  if reviewer.id == player.id: return Game.objects.none()
  return Game.objects.filter(status=Game.Status.COMPLETED,game_players__user=reviewer,game_players__status=GamePlayer.Status.CONFIRMED).filter(game_players__user=player,game_players__status=GamePlayer.Status.CONFIRMED).distinct()
class PlayerStatisticsService:
 @staticmethod
 def statistics(*,user,sport=None):
  games=GamePlayer.objects.filter(user=user,status='confirmed',game__status=Game.Status.COMPLETED)
  if sport: games=games.filter(game__sport=sport)
  completed=games.count(); events=EventParticipant.objects.filter(user=user,status='registered',event__status=Game.Status.COMPLETED).count()
  rating=RatingService.aggregate(user)
  return {'games_played':completed,'games_completed':completed,'games_won':0,'games_lost':0,'games_drawn':0,'win_rate':0,'participations':completed,'events_joined':events,**rating,'recent_form':[]}
 @staticmethod
 def sport_statistics(*,user,sport):
  payload=PlayerStatisticsService.statistics(user=user,sport=sport);entry,_=PlayerSportStatistics.objects.update_or_create(user=user,sport=sport,defaults={'statistics':payload});return entry.statistics
class LeaderboardService:
 @staticmethod
 def ratings(*,sport=None,city=None,limit=20):
  from my_app.models import User
  q=User.objects.filter(is_active=True)
  if city:q=q.filter(city__iexact=city)
  if sport:q=q.filter(user_sports__sport=sport)
  return q.annotate(avg_rating=Avg('ratings_received__rating',filter=Q(ratings_received__deleted_at__isnull=True)),rating_count=Count('ratings_received',filter=Q(ratings_received__deleted_at__isnull=True))).filter(rating_count__gt=0).order_by('-avg_rating','-rating_count')[:limit]
