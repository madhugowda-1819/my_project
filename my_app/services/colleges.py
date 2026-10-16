"""College verification and inter-college competition rules."""
from django.db import IntegrityError, transaction
from django.utils import timezone
from django.utils.text import slugify
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from my_app.models import (
    College, CollegeChallenge, CollegeMembership, CollegeResultDispute,
    Community, CommunityMember, Tournament, TournamentMatch, TournamentTeam,
    TournamentTeamMember,
)
from my_app.services.events import EventService, TournamentMatchService
from my_app.services.notifications import NotificationService
from my_app.utils import create_notification


class CollegeService:
    @staticmethod
    def membership(college, user, *, admin=False):
        member = CollegeMembership.objects.filter(
            college=college, user=user, status=CollegeMembership.Status.ACTIVE,
        ).first()
        if not member or (admin and member.role != CollegeMembership.Role.ADMIN):
            raise PermissionDenied('Active college administrator membership is required.' if admin else 'Active college membership is required.')
        return member

    @classmethod
    @transaction.atomic
    def submit(cls, *, actor, name, city, state='', country='India', description='', approved_email_domain='', verification_evidence=''):
        name, city, country = name.strip(), city.strip(), country.strip()
        if College.objects.filter(name__iexact=name, city__iexact=city, country__iexact=country).exists():
            raise ValidationError({'name': 'A college with this name and location has already been submitted.'})
        base = slugify(name)[:190] or 'college'
        slug, suffix = base, 2
        while College.objects.filter(slug=slug).exists():
            ending = f'-{suffix}'
            slug = f'{base[:200-len(ending)]}{ending}'
            suffix += 1
        college = College.objects.create(
            name=name, slug=slug, city=city, state=state.strip(),
            country=country, description=description.strip(),
            approved_email_domain=approved_email_domain.strip().lower().lstrip('@'),
            verification_evidence=verification_evidence, submitted_by=actor,
        )
        CollegeMembership.objects.create(college=college, user=actor, role=CollegeMembership.Role.ADMIN, status=CollegeMembership.Status.PENDING)
        return college

    @classmethod
    @transaction.atomic
    def review(cls, *, college, actor, approve, reason=''):
        college = College.objects.select_for_update().get(pk=college.pk)
        if college.verification_status != College.VerificationStatus.PENDING:
            raise ValidationError('Only pending college submissions can be reviewed.')
        college.verification_status = College.VerificationStatus.VERIFIED if approve else College.VerificationStatus.REJECTED
        college.reviewed_by, college.reviewed_at = actor, timezone.now()
        college.rejection_reason = '' if approve else reason.strip()
        college.save(update_fields=['verification_status', 'reviewed_by', 'reviewed_at', 'rejection_reason', 'updated_at'])
        membership = CollegeMembership.objects.select_for_update().filter(college=college, user=college.submitted_by).first()
        if membership:
            membership.status = CollegeMembership.Status.ACTIVE if approve else CollegeMembership.Status.REJECTED
            membership.save(update_fields=['status', 'updated_at'])
        NotificationService.create_notification(
            user=college.submitted_by, type='message',
            title='College verification update',
            body=(f'{college.name} was verified.' if approve else f'{college.name} was not verified. {college.rejection_reason}').strip(),
            data={'college_id': str(college.public_id), 'verification_status': college.verification_status},
            event_key=f'college-review:{college.pk}:{college.verification_status}',
        )
        return college

    @classmethod
    @transaction.atomic
    def request_membership(cls, *, college, user, requested_role='player'):
        college = College.objects.select_for_update().get(pk=college.pk)
        if college.verification_status != College.VerificationStatus.VERIFIED:
            raise ValidationError('You can request membership only in a verified college.')
        existing = CollegeMembership.objects.select_for_update().filter(college=college, user=user).first()
        if existing and existing.status in (CollegeMembership.Status.PENDING, CollegeMembership.Status.ACTIVE):
            return existing, False
        if existing:
            existing.status, existing.role = CollegeMembership.Status.PENDING, CollegeMembership.Role.PLAYER
            existing.save(update_fields=['status', 'role', 'updated_at'])
        else:
            existing = CollegeMembership.objects.create(college=college, user=user, status=CollegeMembership.Status.PENDING)
        for admin in CollegeMembership.objects.filter(college=college, role=CollegeMembership.Role.ADMIN, status=CollegeMembership.Status.ACTIVE).select_related('user'):
            create_notification(user=admin.user, type='message', title='College membership request', body=f'{user.username} requested to join {college.name}.', data={'college_id': college.public_id.hex, 'user_id': user.id})
        return existing, True

    @classmethod
    @transaction.atomic
    def decide_membership(cls, *, college, actor, user_id, approve):
        college = College.objects.select_for_update().get(pk=college.pk)
        cls.membership(college, actor, admin=True)
        member = CollegeMembership.objects.select_for_update().filter(college=college, user_id=user_id, status=CollegeMembership.Status.PENDING).first()
        if not member:
            raise NotFound('Pending college membership not found.')
        member.status = CollegeMembership.Status.ACTIVE if approve else CollegeMembership.Status.REJECTED
        member.save(update_fields=['status', 'updated_at'])
        create_notification(user=member.user, type='message', title='College membership update', body=f'Your request to join {college.name} was {"approved" if approve else "declined"}.', data={'college_id': college.public_id.hex})
        return member

    @classmethod
    @transaction.atomic
    def create_club(cls, *, college, actor, name, sport, **kwargs):
        cls.membership(college, actor, admin=True)
        if college.verification_status != College.VerificationStatus.VERIFIED:
            raise PermissionDenied('Only verified colleges may create official college clubs.')
        community = Community.objects.create(
            name=name.strip(), slug=cls._unique_slug(name), description=kwargs.get('description', ''),
            owner=actor, sport=sport, city=college.city, visibility=kwargs.get('visibility', 'public'),
            is_club=True, membership_open=kwargs.get('membership_open', True), college=college,
        )
        CommunityMember.objects.create(community=community, user=actor, role=CommunityMember.Role.OWNER, status=CommunityMember.Status.ACTIVE, joined_at=timezone.now())
        Community.objects.filter(pk=community.pk).update(member_count=1)
        return community

    @staticmethod
    def _unique_slug(name):
        base = slugify(name)[:160] or 'college-club'
        slug, suffix = base, 2
        while Community.objects.filter(slug=slug).exists():
            ending = f'-{suffix}'
            slug = f'{base[:170-len(ending)]}{ending}'
            suffix += 1
        return slug

    @classmethod
    @transaction.atomic
    def create_challenge(cls, *, challenger, challenged, actor, sport, message=''):
        cls.membership(challenger, actor, admin=True)
        if challenger.pk == challenged.pk:
            raise ValidationError('A college cannot challenge itself.')
        if any(c.verification_status != College.VerificationStatus.VERIFIED for c in (challenger, challenged)):
            raise ValidationError('Both colleges must be verified to create a challenge.')
        try:
            challenge = CollegeChallenge.objects.create(challenger=challenger, challenged=challenged, sport=sport, message=message.strip(), created_by=actor)
        except IntegrityError as exc:
            raise ValidationError('A pending challenge for this college and sport already exists.') from exc
        for admin in CollegeMembership.objects.filter(college=challenged, role=CollegeMembership.Role.ADMIN, status=CollegeMembership.Status.ACTIVE).select_related('user'):
            create_notification(user=admin.user, type='message', title='College challenge received', body=f'{challenger.name} challenged {challenged.name} in {sport.name}.', data={'challenge_id': challenge.public_id.hex})
        return challenge

    @classmethod
    @transaction.atomic
    def answer_challenge(cls, *, challenge, actor, accept, venue, start_date, end_date, registration_deadline):
        challenge = CollegeChallenge.objects.select_for_update().select_related('challenger', 'challenged', 'sport').get(pk=challenge.pk)
        cls.membership(challenge.challenged, actor, admin=True)
        if challenge.status != CollegeChallenge.Status.PENDING:
            raise ValidationError('This challenge has already been answered.')
        if not accept:
            challenge.status = CollegeChallenge.Status.DECLINED
            challenge.save(update_fields=['status', 'updated_at'])
            return challenge
        if any(college.verification_status != College.VerificationStatus.VERIFIED for college in (challenge.challenger, challenge.challenged)):
            raise ValidationError('Both colleges must remain verified to accept this challenge.')
        if start_date < timezone.localdate() or end_date < start_date or registration_deadline <= timezone.now() or registration_deadline.date() > start_date:
            raise ValidationError('Challenge fixture dates or registration deadline are invalid.')
        community = challenge.challenger.clubs.filter(is_active=True).first()
        tournament = Tournament.objects.create(
            tournament_reference=EventService.ref('TR'), name=f'{challenge.challenger.name} vs {challenge.challenged.name}',
            organizer=actor, sport=challenge.sport, community=community, venue=venue,
            start_date=start_date, end_date=end_date, registration_deadline=registration_deadline,
            maximum_teams=2, format=Tournament.Format.ROUND_ROBIN, status=Tournament.Status.OPEN, is_inter_college=True,
        )
        challenge.status, challenge.tournament = CollegeChallenge.Status.ACCEPTED, tournament
        challenge.save(update_fields=['status', 'tournament', 'updated_at'])
        return challenge

    @classmethod
    @transaction.atomic
    def create_team(cls, *, tournament, college, actor, name):
        tournament = Tournament.objects.select_for_update().select_related('college_challenge').get(pk=tournament.pk)
        college = College.objects.select_for_update().get(pk=college.pk)
        if not tournament.is_inter_college or not hasattr(tournament, 'college_challenge'):
            raise ValidationError('Tournament is not an inter-college competition.')
        if tournament.status != Tournament.Status.OPEN or timezone.now() >= tournament.registration_deadline:
            raise ValidationError('The tournament team registration period is closed.')
        cls.membership(college, actor, admin=True)
        if college.verification_status != College.VerificationStatus.VERIFIED:
            raise PermissionDenied('Verified college membership is required.')
        challenge = tournament.college_challenge
        if college not in (challenge.challenger, challenge.challenged):
            raise PermissionDenied('Only the participating colleges may register a team.')
        if college.verification_status != College.VerificationStatus.VERIFIED:
            raise PermissionDenied('College verification is required for tournament registration.')
        if tournament.teams.count() >= tournament.maximum_teams:
            raise ValidationError('This tournament has reached its team capacity.')
        if tournament.teams.filter(college=college).exists():
            raise ValidationError('This college already has a team in the tournament.')
        try:
            team = TournamentTeam.objects.create(tournament=tournament, college=college, name=name.strip(), captain=actor, created_by=actor, seed=tournament.teams.count()+1)
        except IntegrityError as exc:
            raise ValidationError('A team with this name is already registered.') from exc
        TournamentTeamMember.objects.create(team=team, user=actor, role=TournamentTeamMember.Role.CAPTAIN)
        return team

    @classmethod
    @transaction.atomic
    def add_roster_member(cls, *, team, actor, user):
        tournament = Tournament.objects.select_for_update().get(pk=team.tournament_id)
        team = TournamentTeam.objects.select_for_update().select_related('college').get(pk=team.pk)
        team.tournament = tournament
        if not team.college_id or not team.tournament.is_inter_college:
            raise ValidationError('This is not a college team.')
        if team.status != TournamentTeam.Status.ACTIVE:
            raise ValidationError('This team is not accepting roster members.')
        if team.college.verification_status != College.VerificationStatus.VERIFIED:
            raise ValidationError('The college must remain verified to update its tournament roster.')
        cls.membership(team.college, actor, admin=True)
        cls.membership(team.college, user)
        if team.tournament.status != Tournament.Status.OPEN or timezone.now() >= team.tournament.registration_deadline:
            raise ValidationError('The tournament roster registration period is closed.')
        if team.tournament.teams.filter(members__user=user, members__status=TournamentTeamMember.Status.ACTIVE).exists():
            raise ValidationError('A player cannot be registered for multiple teams in this tournament.')
        try:
            return TournamentTeamMember.objects.create(team=team, user=user)
        except IntegrityError as exc:
            raise ValidationError('Player is already on this team.') from exc

    @classmethod
    @transaction.atomic
    def generate_bracket(cls, *, tournament, actor):
        if tournament.is_inter_college:
            challenge = getattr(tournament, 'college_challenge', None)
            if not challenge or challenge.status != CollegeChallenge.Status.ACCEPTED:
                raise ValidationError('An accepted inter-college challenge is required.')
            teams = list(tournament.teams.filter(status=TournamentTeam.Status.ACTIVE).select_related('college').order_by('seed', 'created_at'))
            if len(teams) != 2 or {team.college_id for team in teams} != {challenge.challenger_id, challenge.challenged_id}:
                raise ValidationError('Both colleges must register exactly one eligible team before fixtures are created.')
            for team in teams:
                if team.college.verification_status != College.VerificationStatus.VERIFIED:
                    raise ValidationError('Only verified colleges may participate.')
                members = team.members.filter(status=TournamentTeamMember.Status.ACTIVE)
                if not members.exists() or members.exclude(user__college_memberships__college=team.college, user__college_memberships__status=CollegeMembership.Status.ACTIVE).exists():
                    raise ValidationError('Every rostered player must have active membership at their represented college.')
        return TournamentBracketService.generate(tournament=tournament, actor=actor)

    @classmethod
    @transaction.atomic
    def record_result(cls, *, match, actor, score_a, score_b):
        tournament = Tournament.objects.select_for_update().get(pk=match.tournament_id)
        match = TournamentMatch.objects.select_for_update().select_related('team_a', 'team_b').get(pk=match.pk)
        match.tournament = tournament
        if tournament.is_inter_college:
            if tournament.status in (Tournament.Status.CANCELLED, Tournament.Status.COMPLETED):
                raise ValidationError('Results cannot be changed for a cancelled or completed tournament.')
            if match.status == TournamentMatch.Status.COMPLETED:
                raise ValidationError('This result has already been submitted. Use the dispute process to challenge it.')
            if not match.team_a_id or not match.team_b_id or match.team_a.college_id == match.team_b.college_id:
                raise ValidationError('A college fixture must have two distinct eligible college teams.')
            college_admin = CollegeMembership.objects.filter(
                user=actor, role=CollegeMembership.Role.ADMIN, status=CollegeMembership.Status.ACTIVE,
                college__in=[match.team_a.college, match.team_b.college],
                college__verification_status=College.VerificationStatus.VERIFIED,
            ).exists()
            if not college_admin and tournament.organizer_id != actor.id and not actor.is_staff:
                raise PermissionDenied('Only the tournament organizer or a participating college administrator can submit results.')
            # Existing tournament permission checks allow organizer or staff;
            # a verified participating college admin also has result authority.
            try:
                return TournamentMatchService.result(match=match, actor=actor, score_a=score_a, score_b=score_b)
            except PermissionDenied:
                if not college_admin:
                    raise
                return CollegeService._record_result(match, score_a, score_b)
        return TournamentMatchService.result(match=match, actor=actor, score_a=score_a, score_b=score_b)

    @staticmethod
    def _record_result(match, score_a, score_b):
        from rest_framework.exceptions import ValidationError
        try:
            score_a, score_b = int(score_a), int(score_b)
        except (TypeError, ValueError) as exc:
            raise ValidationError('Scores must be integers.') from exc
        if score_a < 0 or score_b < 0 or score_a == score_b:
            raise ValidationError('Scores must be non-negative and decisive.')
        match.score_a, match.score_b = score_a, score_b
        match.winner = match.team_a if score_a > score_b else match.team_b
        match.status = TournamentMatch.Status.COMPLETED
        match.save(update_fields=['score_a', 'score_b', 'winner', 'status', 'updated_at'])
        return match

    @classmethod
    @transaction.atomic
    def dispute_result(cls, *, match, actor, reason):
        if match.status != TournamentMatch.Status.COMPLETED or not match.tournament.is_inter_college:
            raise ValidationError('Only completed college fixtures can be disputed.')
        eligible = TournamentTeamMember.objects.filter(
            team__in=[match.team_a, match.team_b], user=actor, status=TournamentTeamMember.Status.ACTIVE,
        ).exists()
        if not eligible:
            raise PermissionDenied('Only a participating player may dispute this result.')
        if match.college_disputes.filter(status=CollegeResultDispute.Status.OPEN).exists():
            raise ValidationError('An open dispute already exists for this result.')
        dispute = CollegeResultDispute.objects.create(match=match, raised_by=actor, reason=reason.strip())
        for team in (match.team_a, match.team_b):
            if team:
                for admin in CollegeMembership.objects.filter(college=team.college, role=CollegeMembership.Role.ADMIN, status=CollegeMembership.Status.ACTIVE).select_related('user'):
                    create_notification(user=admin.user, type='message', title='College result disputed', body=f'A player disputed the result for {team.tournament.name}.', data={'match_id': str(match.public_id), 'dispute_id': str(dispute.public_id)})
        return dispute

    @staticmethod
    @transaction.atomic
    def resolve_dispute(*, dispute, actor, resolution, dismiss=False):
        dispute = CollegeResultDispute.objects.select_for_update().get(pk=dispute.pk)
        if dispute.status != CollegeResultDispute.Status.OPEN:
            raise ValidationError('This dispute is already closed.')
        dispute.status = CollegeResultDispute.Status.DISMISSED if dismiss else CollegeResultDispute.Status.RESOLVED
        dispute.resolution, dispute.resolved_by, dispute.resolved_at = resolution.strip(), actor, timezone.now()
        dispute.save(update_fields=['status', 'resolution', 'resolved_by', 'resolved_at', 'updated_at'])
        return dispute
