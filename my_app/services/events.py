"""Transactional event registration and deterministic tournament operations."""
import math, secrets
from datetime import timedelta
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from my_app.models import Event, EventParticipant, EventReminder, Tournament, TournamentTeam, TournamentTeamMember, TournamentMatch, UserSport
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
  if event.visibility==Event.Visibility.PRIVATE and event.organizer_id!=user.id: raise PermissionDenied('This event is private.')
  if event.visibility==Event.Visibility.COMMUNITY and (not event.community_id or not event.community.members.filter(user=user,status='active').exists()): raise PermissionDenied('Join the event community before registering.')
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
  event=Event.objects.select_for_update().get(pk=event.pk)
  if event.starts_at<=timezone.now() or event.status in (Event.Status.CANCELLED,Event.Status.STARTED,Event.Status.COMPLETED): raise EventError('Event registration can no longer be cancelled.')
  p=EventParticipant.objects.select_for_update().filter(event=event,user=user).first()
  if not p or p.status not in (EventParticipant.Status.REGISTERED,EventParticipant.Status.WAITLISTED): raise NotFound('Active event registration not found.')
  p.status=EventParticipant.Status.REMOVED if removed else EventParticipant.Status.CANCELLED; p.left_at=timezone.now(); p.save(update_fields=['status','left_at','updated_at'])
  if not removed: EventReminder.objects.filter(event=event,user=user,sent_at__isnull=True,cancelled_at__isnull=True).update(cancelled_at=timezone.now(),updated_at=timezone.now())
  if not removed:
   nxt=EventParticipant.objects.select_for_update().filter(event=event,status=EventParticipant.Status.WAITLISTED).order_by('joined_at','id').first()
   if nxt: nxt.status=EventParticipant.Status.REGISTERED; nxt.save(update_fields=['status','updated_at']); create_notification(user=nxt.user,type='message',title='Event waitlist promotion',body=f'You are registered for {event.title}.',data={'event_id':event.public_id.hex})
  EventCapacityService.refresh(event); return p


class EventReminderService:
 @classmethod
 @transaction.atomic
 def set_reminder(cls,*,event,user,minutes_before=60):
  event=Event.objects.select_for_update().get(pk=event.pk)
  if minutes_before not in (15,60,1440): raise ValidationError('Reminder must be 15 minutes, 1 hour, or 1 day before the event.')
  if event.status not in (Event.Status.OPEN,Event.Status.FULL) or event.starts_at<=timezone.now(): raise EventError('Reminders are available only for upcoming events.')
  if not EventParticipant.objects.filter(event=event,user=user,status=EventParticipant.Status.REGISTERED).exists(): raise PermissionDenied('Register for the event before setting a reminder.')
  remind_at=event.starts_at-timedelta(minutes=minutes_before)
  if remind_at<=timezone.now(): remind_at=timezone.now()
  reminder,created=EventReminder.objects.get_or_create(event=event,user=user,defaults={'remind_at':remind_at})
  if not created:
   reminder.remind_at=remind_at; reminder.sent_at=None; reminder.cancelled_at=None
   reminder.save(update_fields=['remind_at','sent_at','cancelled_at','updated_at'])
  return reminder

 @classmethod
 @transaction.atomic
 def cancel_reminder(cls,*,event,user):
  reminder=EventReminder.objects.select_for_update().filter(event=event,user=user,cancelled_at__isnull=True,sent_at__isnull=True).first()
  if reminder:
   reminder.cancelled_at=timezone.now();reminder.save(update_fields=['cancelled_at','updated_at'])
  return reminder

 @classmethod
 @transaction.atomic
 def deliver_due(cls,*,now=None,limit=500):
  now=now or timezone.now(); sent=0
  reminders=list(EventReminder.objects.select_for_update(skip_locked=True).filter(sent_at__isnull=True,cancelled_at__isnull=True,remind_at__lte=now,event__status__in=[Event.Status.OPEN,Event.Status.FULL]).select_related('event','user').order_by('remind_at')[:limit])
  for reminder in reminders:
   if not EventParticipant.objects.filter(event=reminder.event,user=reminder.user,status=EventParticipant.Status.REGISTERED).exists():
    reminder.cancelled_at=now;reminder.save(update_fields=['cancelled_at','updated_at']);continue
   create_notification(user=reminder.user,type='reminder',title='Event reminder',body=f'{reminder.event.title} starts at {reminder.event.starts_at:%b %d, %Y %I:%M %p}.',data={'event_id':reminder.event.public_id.hex,'starts_at':reminder.event.starts_at.isoformat()},priority=2)
   reminder.sent_at=now;reminder.save(update_fields=['sent_at','updated_at']);sent+=1
  return sent


class EventCancellationService:
 @classmethod
 @transaction.atomic
 def cancel(cls,*,event,actor):
  event=Event.objects.select_for_update().get(pk=event.pk)
  if event.organizer_id!=actor.id and not actor.is_staff: raise PermissionDenied('Only the event organizer can cancel.')
  if event.status in (Event.Status.STARTED,Event.Status.COMPLETED): raise EventError('Started or completed events cannot be cancelled.')
  if event.status==Event.Status.CANCELLED:return event
  event.status=Event.Status.CANCELLED;event.save(update_fields=['status','updated_at'])
  participants=EventParticipant.objects.select_for_update().filter(event=event,status__in=[EventParticipant.Status.REGISTERED,EventParticipant.Status.WAITLISTED]).select_related('user')
  for participant in participants:
   create_notification(user=participant.user,type='message',title='Event cancelled',body=f'{event.title} has been cancelled by its organizer.',data={'event_id':event.public_id.hex})
   participant.status=EventParticipant.Status.CANCELLED;participant.left_at=timezone.now();participant.save(update_fields=['status','left_at','updated_at'])
  EventReminder.objects.filter(event=event,sent_at__isnull=True,cancelled_at__isnull=True).update(cancelled_at=timezone.now(),updated_at=timezone.now())
  return event
class TournamentService:
 @classmethod
 @transaction.atomic
 def create_team(cls,*,tournament,creator,name):
  if tournament.organizer_id!=creator.id and not creator.is_staff: raise PermissionDenied('Only organizer can create teams.')
  tournament=Tournament.objects.select_for_update().get(pk=tournament.pk)
  if tournament.status not in (Tournament.Status.OPEN,Tournament.Status.FULL) or tournament.registration_deadline<=timezone.now(): raise EventError('Tournament registration is closed.')
  active_count=tournament.teams.filter(status=TournamentTeam.Status.ACTIVE).count()
  if active_count>=tournament.maximum_teams: raise EventError('Tournament team capacity is full.')
  team=TournamentTeam.objects.create(tournament=tournament,name=name.strip(),captain=creator,created_by=creator,seed=active_count+1)
  TournamentTeamMember.objects.create(team=team,user=creator,role=TournamentTeamMember.Role.CAPTAIN); return team

class TournamentRegistrationService:
 @classmethod
 @transaction.atomic
 def register(cls,*,tournament,user,team_name):
  tournament=Tournament.objects.select_for_update().get(pk=tournament.pk)
  if tournament.is_inter_college: raise PermissionDenied('Register college teams through the college competition flow.')
  if tournament.status not in (Tournament.Status.OPEN,Tournament.Status.FULL) or tournament.registration_deadline<=timezone.now(): raise EventError('Tournament registration is closed.')
  existing=TournamentTeamMember.objects.select_for_update().filter(team__tournament=tournament,user=user,status=TournamentTeamMember.Status.ACTIVE).first()
  if existing: return existing.team,False
  if tournament.teams.filter(status=TournamentTeam.Status.ACTIVE).count()>=tournament.maximum_teams: raise EventError('Tournament team capacity is full.')
  if tournament.skill_level:
   try: level=UserSport.objects.get(user=user,sport=tournament.sport).skill_level
   except UserSport.DoesNotExist as exc: raise EventError('Add this sport before registering.') from exc
   if level!=tournament.skill_level: raise EventError('Skill level is not eligible.')
  name=team_name.strip()
  if not name: name=f'{user.full_name or user.username} Team'[:120]
  active_count=tournament.teams.filter(status=TournamentTeam.Status.ACTIVE).count()
  team=TournamentTeam.objects.create(tournament=tournament,name=name,captain=user,created_by=user,seed=active_count+1)
  TournamentTeamMember.objects.create(team=team,user=user,role=TournamentTeamMember.Role.CAPTAIN)
  if tournament.teams.filter(status=TournamentTeam.Status.ACTIVE).count()>=tournament.maximum_teams:
   tournament.status=Tournament.Status.FULL;tournament.save(update_fields=['status','updated_at'])
  return team,True

 @classmethod
 @transaction.atomic
 def leave(cls,*,tournament,user):
  tournament=Tournament.objects.select_for_update().get(pk=tournament.pk)
  if tournament.is_inter_college: raise PermissionDenied('College team membership must be managed by the college administrator.')
  if tournament.status not in (Tournament.Status.OPEN,Tournament.Status.FULL) or tournament.registration_deadline<=timezone.now(): raise EventError('Tournament registration is closed.')
  member=TournamentTeamMember.objects.select_for_update().filter(team__tournament=tournament,user=user,status=TournamentTeamMember.Status.ACTIVE).select_related('team').first()
  if not member: raise NotFound('Tournament registration not found.')
  team=member.team
  member.status=TournamentTeamMember.Status.LEFT;member.save(update_fields=['status','updated_at'])
  if team.captain_id==user.id and not team.members.filter(status=TournamentTeamMember.Status.ACTIVE).exists(): team.status=TournamentTeam.Status.WITHDRAWN;team.save(update_fields=['status','updated_at'])
  if tournament.status==Tournament.Status.FULL:
   tournament.status=Tournament.Status.OPEN;tournament.save(update_fields=['status','updated_at'])
  return team
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
   # Build every round up front.  This gives the organizer a complete
   # quarter-final/semi-final/final schedule as soon as the bracket starts.
   size=2**math.ceil(math.log2(len(teams)))
   rounds=int(math.log2(size))
   for round_number in range(1,rounds+1):
    for match_number in range(1,(size//(2**round_number))+1):
     TournamentMatch.objects.create(tournament=tournament,round_number=round_number,match_number=match_number)
   for index,team in enumerate(teams):
    match=TournamentMatch.objects.get(tournament=tournament,round_number=1,match_number=(index//2)+1)
    if index%2==0: match.team_a=team
    else: match.team_b=team
    match.save(update_fields=['team_a','team_b','updated_at'])
   cls.resolve_walkovers(tournament)
  tournament.status=Tournament.Status.STARTED; tournament.save(update_fields=['status','updated_at']); return tournament.matches.all()

 @staticmethod
 def next_match(match):
  return TournamentMatch.objects.filter(
   tournament=match.tournament,round_number=match.round_number+1,
   match_number=(match.match_number+1)//2,
  ).first()

 @classmethod
 def advance(cls,match,winner):
  next_match=cls.next_match(match)
  if next_match is None:
   winner.status=TournamentTeam.Status.CHAMPION
   winner.save(update_fields=['status','updated_at'])
   match.tournament.status=Tournament.Status.COMPLETED
   match.tournament.save(update_fields=['status','updated_at'])
   return
  if match.match_number%2: next_match.team_a=winner
  else: next_match.team_b=winner
  next_match.save(update_fields=['team_a','team_b','updated_at'])

 @classmethod
 def resolve_walkovers(cls,tournament):
  """Advance byes only after both feeder matches have been decided."""
  changed=True
  while changed:
   changed=False
   for match in tournament.matches.filter(status=TournamentMatch.Status.SCHEDULED).order_by('round_number','match_number'):
    feeders=TournamentMatch.objects.filter(tournament=tournament,round_number=match.round_number-1,match_number__in=[match.match_number*2-1,match.match_number*2]) if match.round_number>1 else TournamentMatch.objects.none()
    if feeders.exists() and feeders.exclude(status__in=[TournamentMatch.Status.COMPLETED,TournamentMatch.Status.CANCELLED]).exists(): continue
    if match.team_a_id and match.team_b_id: continue
    if not match.team_a_id and not match.team_b_id:
     match.status=TournamentMatch.Status.CANCELLED
     match.save(update_fields=['status','updated_at'])
     changed=True
     continue
    winner=match.team_a or match.team_b
    match.winner=winner;match.status=TournamentMatch.Status.COMPLETED
    match.save(update_fields=['winner','status','updated_at'])
    cls.advance(match,winner)
    changed=True
class TournamentMatchService:
 @classmethod
 @transaction.atomic
 def result(cls,*,match,actor,score_a,score_b):
  if match.tournament.organizer_id!=actor.id and not actor.is_staff: raise PermissionDenied('Only organizer can submit results.')
  if match.status!=TournamentMatch.Status.SCHEDULED or not match.team_a_id or not match.team_b_id: raise ValidationError('Both teams must be ready before recording a result.')
  try: score_a,score_b=int(score_a),int(score_b)
  except (TypeError,ValueError) as exc: raise ValidationError('Scores must be integers.') from exc
  if score_a<0 or score_b<0 or score_a==score_b: raise ValidationError('Scores must be non-negative and decisive.')
  match.score_a,match.score_b=score_a,score_b; match.winner=match.team_a if score_a>score_b else match.team_b; match.status=TournamentMatch.Status.COMPLETED; match.save(update_fields=['score_a','score_b','winner','status','updated_at'])
  if match.tournament.format!=Tournament.Format.ROUND_ROBIN:
   loser=match.team_b if match.winner_id==match.team_a_id else match.team_a
   loser.status=TournamentTeam.Status.ELIMINATED;loser.save(update_fields=['status','updated_at'])
   TournamentBracketService.advance(match,match.winner)
   TournamentBracketService.resolve_walkovers(match.tournament)
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
