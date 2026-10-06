"""Permission-aware deterministic global search; deliberately not AI search."""
from django.db.models import Q
from my_app.models import Community, Event, Game, Sport, Tournament, User, Venue
from my_app.services.locations import nearby_queryset, validate_coordinates

VALID_TYPES={'player','sport','venue','game','community','event','tournament'}
class GlobalSearchService:
 @staticmethod
 def score(title,description,q,popularity=0,distance=None):
  title=(title or '').lower();description=(description or '').lower();q=q.lower();s=40 if title==q else 30 if title.startswith(q) else 20 if q in title else 10 if q in description else 0
  if popularity:s+=min(10,popularity)
  if distance is not None:s+=10 if distance<=2 else 5 if distance<=10 else 0
  return s
 @classmethod
 def search(cls,*,user,q,types,city=None,latitude=None,longitude=None,radius=20):
  q=q.strip()
  if not q:return []
  types=set(types or VALID_TYPES)&VALID_TYPES
  location=latitude is not None
  if location:latitude,longitude,radius=validate_coordinates(latitude,longitude,radius)
  results=[]
  def add(kind,items,title,description='',image='',popularity=lambda x:0):
   for obj in items:
    distance=getattr(obj,'distance_km',None);score=cls.score(title(obj),description(obj),q,popularity(obj),distance)
    if score:results.append({'type':kind,'id':str(obj.public_id),'title':title(obj),'description':description(obj),'image':str(getattr(obj,image,'')) if image else None,'metadata':({'distance_km':round(distance,2)} if distance is not None else {}),'_score':score})
  text=Q(name__icontains=q)|Q(description__icontains=q)
  if 'sport'in types:add('sport',Sport.objects.filter(Q(name__icontains=q)|Q(description__icontains=q),is_active=True)[:50],lambda x:x.name,lambda x:x.description)
  if 'player'in types:add('player',User.objects.filter(Q(username__icontains=q)|Q(full_name__icontains=q),is_active=True,account_status='active')[:50],lambda x:x.full_name or x.username,lambda x:x.city)
  venues=Venue.objects.filter(text,active=True)
  if location:venues=nearby_queryset(venues,latitude=latitude,longitude=longitude,radius=radius)
  if city:venues=venues.filter(city__iexact=city)
  if 'venue'in types:add('venue',venues[:100],lambda x:x.name,lambda x:x.description,'',lambda x:float(x.rating))
  games=Game.objects.filter(Q(sport__name__icontains=q)|Q(venue__name__icontains=q)|Q(description__icontains=q),visibility='public',status__in=['open','almost_full']).select_related('sport','venue')
  if location:games=nearby_queryset(games,latitude=latitude,longitude=longitude,radius=radius,latitude_field='venue__latitude',longitude_field='venue__longitude')
  if 'game'in types:add('game',games[:100],lambda x:f'{x.sport.name} game',lambda x:x.description)
  communities=Community.objects.filter(text,is_active=True,visibility='public')
  if location:communities=nearby_queryset(communities,latitude=latitude,longitude=longitude,radius=radius)
  if city:communities=communities.filter(city__iexact=city)
  if 'community'in types:add('community',communities[:100],lambda x:x.name,lambda x:x.description,'avatar',lambda x:min(10,x.member_count))
  events=Event.objects.filter(Q(title__icontains=q)|Q(description__icontains=q)|Q(sport__name__icontains=q),visibility='public',status__in=['open','full']).select_related('sport','venue')
  if location:events=nearby_queryset(events,latitude=latitude,longitude=longitude,radius=radius,latitude_field='venue__latitude',longitude_field='venue__longitude')
  if 'event'in types:add('event',events[:100],lambda x:x.title,lambda x:x.description,'image')
  if 'tournament'in types:add('tournament',Tournament.objects.filter(Q(name__icontains=q)|Q(sport__name__icontains=q),status__in=['open','full','started']).select_related('sport')[:100],lambda x:x.name,lambda x:x.sport.name)
  return sorted(results,key=lambda r:(-r['_score'],r['title'].lower()))
