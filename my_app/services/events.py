"""Transactional event registration and deterministic tournament operations."""
import math, secrets
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from my_app.models import Event, EventParticipant, Tournament, TournamentTeam, TournamentTeamMember, TournamentMatch, UserSport
from my_app.services.bookings import BookingService
from my_app.utils import create_notification

class EventError(APIException): status_code=400; default_code='EVENT_ERROR'; default_detail='Event request could not be completed.'
class EventCapacityService:
 @staticmethod
 def refresh(event):
  count=event.participants.filter(status=EventParticipant.Status.REGISTERED).count()
  if event.status not in (Event.Status.CANCELLED,Event.Status.COMPLETED,Event.Status.STARTED):
   target=Event.Status.FULL if count>=event.maximum_participants else Event.Status.OPEN
   if event.status!=target: event.status=target; event.save(update_fields=['status','updated_at'])
  return count, max(0,event.maximum_participants-count)
class EventService:
 @staticmethod
 def ref(prefix): return f'{prefix}-{secrets.token_urlsafe(6).upper().replace("_","X").replace("-","Y")}'[:20]
 @classmethod
 @transaction.atomic
 def create(cls,*,organizer,**data):
  venue=data['venue']; court=data.get('court')
  if not venue.active or (court and (not court.active or court.venue_id!=venue.id or court.sport_id!=data['sport'].id)): raise EventError('Invalid active venue/court for this event.')
  starts,ends=BookingService._local_datetimes(venue=venue,booking_date=data['event_date'],start_time=data['start_time'],end_time=data['end_time'])
  if starts<=timezone.now() or data['registration_deadline']>=starts: raise ValidationError('Event must be future and registration deadline must precede it.')
  if court: BookingService._validate_request(venue=venue,court=court,booking_date=data['event_date'],start_time=data['start_time'],end_time=data['end_time'],lock_bookings=True)
  return Event.objects.create(event_reference=cls.ref('EV'),organizer=organizer,starts_at=starts,ends_at=ends,**data)
class EventRegistrationService:
 @classmethod
 @transaction.atomic
 def register(cls,*,event,user):
  event=Event.objects.select_for_update().get(pk=event.pk)
  if event.status not in (Event.Status.OPEN,Event.Status.FULL) or event.registration_deadline<=timezone.now(): raise EventError('Registration is closed.')
  existing=EventParticipant.objects.select_for_update().filter(event=event,user=user).first()
  if existing and existing.status in (EventParticipant.Status.REGISTERED,EventParticipant.Status.WAITLISTED,EventParticipant.Status.REMOVED): return existing,False
  if event.skill_level:
   try: level=UserSport.objects.get(user=user,sport=event.sport).skill_level
   except UserSport.DoesNotExist as exc: raise EventError('Add this sport before registering.') from exc
   if level!=event.skill_level: raise EventError('Skill level is not eligible.')
  _,slots=EventCapacityService.refresh(event); status=EventParticipant.Status.REGISTERED if slots else EventParticipant.Status.WAITLISTED
  if existing: existing.status=status; existing.left_at=None; existing.save(update_fields=['status','left_at','updated_at']); participant=existing
  else: participant=EventParticipant.objects.create(event=event,user=user,status=status)
  EventCapacityService.refresh(event); create_notification(user=user,type='message',title='Event registration',body=('Registered.' if status=='registered' else 'Added to waitlist.'),data={'event_id':event.public_id.hex})
  return participant,True
 @classmethod
 @transaction.atomic
 def leave(cls,*,event,user,removed=False):
  event=Event.objects.select_for_update().get(pk=event.pk); p=EventParticipant.objects.select_for_update().get(event=event,user=user)
  p.status=EventParticipant.Status.REMOVED if removed else EventParticipant.Status.CANCELLED; p.left_at=timezone.now(); p.save(update_fields=['status','left_at','updated_at'])
  if not removed:
   nxt=EventParticipant.objects.select_for_update().filter(event=event,status=EventParticipant.Status.WAITLISTED).order_by('joined_at','id').first()
   if nxt: nxt.status=EventParticipant.Status.REGISTERED; nxt.save(update_fields=['status','updated_at']); create_notification(user=nxt.user,type='message',title='Event waitlist promotion',body=f'You are registered for {event.title}.',data={'event_id':event.public_id.hex})
  EventCapacityService.refresh(event); return p
class TournamentService:
 @classmethod
 @transaction.atomic
 def create_team(cls,*,tournament,creator,name):
  if tournament.organizer_id!=creator.id and not creator.is_staff: raise PermissionDenied('Only organizer can create teams.')
  team=TournamentTeam.objects.create(tournament=tournament,name=name.strip(),captain=creator,created_by=creator,seed=tournament.teams.count()+1)
  TournamentTeamMember.objects.create(team=team,user=creator,role=TournamentTeamMember.Role.CAPTAIN); return team
class TournamentBracketService:
 @classmethod
 @transaction.atomic
 def generate(cls,*,tournament,actor):
  if tournament.organizer_id!=actor.id and not actor.is_staff: raise PermissionDenied('Only organizer can generate brackets.')
  if tournament.matches.exists(): raise EventError('Bracket already generated.')
  teams=list(tournament.teams.filter(status=TournamentTeam.Status.ACTIVE).order_by('seed','created_at'))
  if len(teams)<2: raise EventError('At least two teams are required.')
  if tournament.format==Tournament.Format.ROUND_ROBIN:
   no=1
   for i,a in enumerate(teams):
    for b in teams[i+1:]: TournamentMatch.objects.create(tournament=tournament,round_number=i+1,match_number=no,team_a=a,team_b=b); no+=1
  else:
   size=2**math.ceil(math.log2(len(teams))); pairs=[]
   for i in range(0,size,2): pairs.append((teams[i] if i<len(teams) else None,teams[i+1] if i+1<len(teams) else None))
   for no,(a,b) in enumerate(pairs,1): TournamentMatch.objects.create(tournament=tournament,round_number=1,match_number=no,team_a=a,team_b=b)
  tournament.status=Tournament.Status.STARTED; tournament.save(update_fields=['status','updated_at']); return tournament.matches.all()
class TournamentMatchService:
 @classmethod
 @transaction.atomic
 def result(cls,*,match,actor,score_a,score_b):
  if match.tournament.organizer_id!=actor.id and not actor.is_staff: raise PermissionDenied('Only organizer can submit results.')
  try: score_a,score_b=int(score_a),int(score_b)
  except (TypeError,ValueError) as exc: raise ValidationError('Scores must be integers.') from exc
  if score_a<0 or score_b<0 or score_a==score_b: raise ValidationError('Scores must be non-negative and decisive.')
  match.score_a,match.score_b=score_a,score_b; match.winner=match.team_a if score_a>score_b else match.team_b; match.status=TournamentMatch.Status.COMPLETED; match.save(update_fields=['score_a','score_b','winner','status','updated_at'])
  if match.tournament.format==Tournament.Format.SINGLE_ELIMINATION and match.round_number==1 and match.tournament.matches.filter(status=TournamentMatch.Status.SCHEDULED).count()==0: match.winner.status=TournamentTeam.Status.CHAMPION; match.winner.save(update_fields=['status','updated_at']); match.tournament.status=Tournament.Status.COMPLETED; match.tournament.save(update_fields=['status','updated_at'])
  return match
class TournamentStandingsService:
 @staticmethod
 def standings(tournament):
  rows={t.id:{'team':t,'played':0,'wins':0,'losses':0,'draws':0,'points':0,'scored':0,'conceded':0} for t in tournament.teams.all()}
  for m in tournament.matches.filter(status=TournamentMatch.Status.COMPLETED):
   if not m.team_a_id or not m.team_b_id: continue
   a,b=rows[m.team_a_id],rows[m.team_b_id]; a['played']+=1;b['played']+=1;a['scored']+=m.score_a;b['scored']+=m.score_b;a['conceded']+=m.score_b;b['conceded']+=m.score_a
   if m.score_a>m.score_b:a['wins']+=1;a['points']+=3;b['losses']+=1
   elif m.score_b>m.score_a:b['wins']+=1;b['points']+=3;a['losses']+=1
   else:a['draws']+=1;b['draws']+=1;a['points']+=1;b['points']+=1
  return sorted([{**r,'difference':r['scored']-r['conceded']} for r in rows.values()],key=lambda r:(-r['points'],-r['difference'],-r['scored']))
