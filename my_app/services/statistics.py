from django.db import IntegrityError, transaction
from django.db.models import Avg, Count, Q
from decimal import Decimal, ROUND_HALF_UP
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from my_app.models import EventParticipant, Game, GamePlayer, PlayerRating, PlayerSportStatistics, TournamentMatch, TournamentTeamMember

class RatingService:
 @staticmethod
 def calculate_overall(*,playing_performance,teamwork,sportsmanship,punctuality):
  return (Decimal(playing_performance+teamwork+sportsmanship+punctuality)/Decimal('4')).quantize(Decimal('0.1'),rounding=ROUND_HALF_UP)
 @classmethod
 def rate_player(cls,*,reviewer,player,game,playing_performance,teamwork,sportsmanship,punctuality,review=''):
  if reviewer.id==player.id: raise ValidationError('You cannot rate yourself.')
  if not player.is_active: raise ValidationError('Player is inactive.')
  if game.status!=Game.Status.COMPLETED or not GamePlayer.objects.filter(game=game,user=reviewer,status='confirmed').exists() or not GamePlayer.objects.filter(game=game,user=player,status='confirmed').exists(): raise PermissionDenied('A completed shared game is required.')
  if len(review)>2000: raise ValidationError({'review':['Review cannot exceed 2000 characters.']})
  # Four equally weighted criteria; persist a predictable one-decimal score.
  overall=cls.calculate_overall(playing_performance=playing_performance,teamwork=teamwork,sportsmanship=sportsmanship,punctuality=punctuality)
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
  completed=games.count()
  events=EventParticipant.objects.filter(user=user,status=EventParticipant.Status.REGISTERED,event__status='completed')
  tournament_members=TournamentTeamMember.objects.filter(user=user)
  tournament_matches=TournamentMatch.objects.filter(status=TournamentMatch.Status.COMPLETED).filter(Q(team_a__members__in=tournament_members)|Q(team_b__members__in=tournament_members))
  if sport:
   events=events.filter(event__sport=sport)
   tournament_members=tournament_members.filter(team__tournament__sport=sport)
   tournament_matches=tournament_matches.filter(tournament__sport=sport)
  tournaments= tournament_members.values('team__tournament_id').distinct().count()
  tournament_wins=tournament_members.filter(team__status='champion',team__tournament__status='completed').values('team__tournament_id').distinct().count()
  events_count=events.values('event_id').distinct().count()
  rating=RatingService.aggregate(user)
  # Court-backed Game has no persisted result/winner, so win/loss/draw and form
  # are intentionally null rather than fabricated zeroes. Tournament results
  # are persisted and can be counted independently.
  return {'games_played':completed,'games_completed':completed,'games_won':None,'games_lost':None,'games_drawn':None,'win_rate':None,'participations':completed,'events_joined':events_count,'events_participated':events_count,'tournaments_participated':tournaments,'tournament_wins':tournament_wins,**rating,'recent_form':None}
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
