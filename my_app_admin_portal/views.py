"""Platform-admin and moderation API controllers."""
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.mail import send_mail
from django.db import transaction
from django.db.models import Q, Count
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import generics
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from my_app.models import (
    College, CollegeResultDispute, Community, CourtBooking, Event, Game,
    PlayerProfile, Venue,
)
from my_app_admin_portal.models import ModerationAction, Report, RoleAuditLog
from my_app_venue_portal.models import VenueOwnerApplication
from my_app.permissions import (
    ADMIN_GROUPS, MODERATOR_GROUPS, VENUE_OWNER_GROUP, IsActiveAccount,
    has_platform_role,
)
from my_app.serializers import (
    CollegeDisputeResolveSerializer, CollegeResultDisputeSerializer,
    CollegeSerializer, VenueSerializer,
)
from my_app_admin_portal.serializers import (
    ReportCreateSerializer, ReportSerializer, ReportUpdateSerializer,
)
from my_app.services.colleges import CollegeService
from my_app_admin_portal.services.moderation import ModerationService
from my_app.services.notifications import NotificationService
from my_app_admin_portal.serializers import AdminUserSerializer, ModerationActionSerializer
from my_app_admin_portal.permissions import IsPlatformAdmin

User = get_user_model()

class AdminVenueManagementView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        queryset = Venue.objects.select_related('owner').prefetch_related('courts', 'venue_sports__sport').order_by('city', 'name')
        search = request.query_params.get('q', '').strip()
        if search:
            queryset = queryset.filter(Q(name__icontains=search) | Q(city__icontains=search) | Q(owner__username__icontains=search))
        active = request.query_params.get('active')
        if active in ('true', 'false'):
            queryset = queryset.filter(active=(active == 'true'))
        queryset = queryset[:200]
        data = VenueSerializer(queryset, many=True, context={'request': request}).data
        for row, venue in zip(data, queryset):
            row['is_demo'] = venue.is_demo
            row['owner'] = ({'public_id': str(venue.owner.public_id), 'username': venue.owner.username, 'email': venue.owner.email} if venue.owner_id else None)
            row['booking_count'] = venue.bookings.count()
        return Response({'success': True, 'data': data})

    @transaction.atomic
    def patch(self, request, public_id):
        venue = get_object_or_404(Venue.objects.select_for_update().select_related('owner'), public_id=public_id)
        changes = request.data
        unknown = set(changes.keys()) - {'active', 'owner_id', 'reason'}
        if unknown:
            raise ValidationError({field: 'This field is not supported for venue operations.' for field in sorted(unknown)})
        reason = str(changes.get('reason', '')).strip()
        if len(reason) > 500:
            raise ValidationError({'reason': 'Use 500 characters or fewer.'})
        changed = False
        if 'active' in changes:
            if not isinstance(changes['active'], bool):
                raise ValidationError({'active': 'Must be true or false.'})
            if changes['active'] and venue.is_demo:
                raise ValidationError({'active': 'Demo venues cannot be enabled for player booking.'})
            if changes['active'] and not venue.active:
                if not venue.owner_id or not venue.venue_sports.exists() or not venue.courts.filter(active=True).exists():
                    raise ValidationError({'active': 'Assign an approved owner, at least one sport, and an active court before approval.'})
            venue.active = changes['active']
            changed = True
        if 'owner_id' in changes:
            requested = changes['owner_id']
            owner = None
            if requested:
                owner = get_object_or_404(User, public_id=requested, is_active=True, account_status=User.AccountStatus.ACTIVE)
                if not owner.groups.filter(name=VENUE_OWNER_GROUP).exists():
                    raise ValidationError({'owner_id': 'Assign only an active approved venue owner.'})
            venue.owner = owner
            changed = True
        if not changed:
            raise ValidationError({'detail': 'Provide active and/or owner_id.'})
        if not reason:
            raise ValidationError({'reason': 'A reason is required for auditable venue changes.'})
        previous_owner = venue.owner
        venue.save(update_fields=['active', 'owner', 'updated_at'])
        audit_target = venue.owner or previous_owner
        if audit_target:
            RoleAuditLog.objects.create(actor=request.user, target_user=audit_target, role='venue_management', action='update', reason=reason)
        if venue.owner_id:
            NotificationService.create_notification(
                user=venue.owner, actor=request.user, type='message',
                title='Venue operations updated',
                body=(f'{venue.name} has been approved and is now bookable.' if venue.active else f'{venue.name} was updated by SportMate administration.'),
                data={'venue_id': venue.public_id.hex, 'active': venue.active},
                event_key=f'venue-admin-update:{venue.pk}:{venue.updated_at.isoformat()}',
            )
        return Response({'success': True, 'data': {
            'public_id': str(venue.public_id), 'active': venue.active,
            'owner_id': str(venue.owner.public_id) if venue.owner_id else None,
        }})


class CollegeVerificationView(APIView):
    permission_classes = [IsPlatformAdmin]

    def post(self, request, public_id):
        college = get_object_or_404(College, public_id=public_id)
        approve = request.data.get('approve')
        if not isinstance(approve, bool):
            raise ValidationError({'approve': 'Must be true or false.'})
        college = CollegeService.review(college=college, actor=request.user, approve=approve, reason=request.data.get('reason', ''))
        return Response({'success': True, 'data': CollegeSerializer(college).data})


class CollegeResultDisputeResolveView(APIView):
    permission_classes = [IsPlatformAdmin]

    def post(self, request, public_id):
        dispute = get_object_or_404(CollegeResultDispute, public_id=public_id)
        serializer = CollegeDisputeResolveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        dispute = CollegeService.resolve_dispute(dispute=dispute, actor=request.user, **serializer.validated_data)
        return Response({'success': True, 'data': CollegeResultDisputeSerializer(dispute).data})


class AdminCollegeResultDisputesView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        queryset = CollegeResultDispute.objects.select_related(
            'match__tournament', 'match__team_a', 'match__team_b', 'raised_by',
        ).order_by('-created_at')
        status = request.query_params.get('status', CollegeResultDispute.Status.OPEN)
        if status not in CollegeResultDispute.Status.values and status != 'all':
            raise ValidationError({'status': ['Choose open, resolved, dismissed, or all.']})
        if status != 'all':
            queryset = queryset.filter(status=status)
        return Response({
            'success': True,
            'data': CollegeResultDisputeSerializer(queryset[:200], many=True).data,
        })


class ReportListCreateView(APIView):
    permission_classes = [IsActiveAccount]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'reports'

    def get(self, request):
        queryset = Report.objects.select_related('reporter', 'assigned_moderator').order_by('-created_at')
        if not has_platform_role(request.user, MODERATOR_GROUPS):
            queryset = queryset.filter(reporter=request.user)
        else:
            if request.query_params.get('status'):
                queryset = queryset.filter(status=request.query_params['status'])
            if request.query_params.get('reason'):
                queryset = queryset.filter(reason=request.query_params['reason'])
            if request.query_params.get('target_type'):
                queryset = queryset.filter(target_type=request.query_params['target_type'])
            if request.query_params.get('moderator'):
                queryset = queryset.filter(assigned_moderator__public_id=request.query_params['moderator'])
            if request.query_params.get('created_after'):
                queryset = queryset.filter(created_at__date__gte=request.query_params['created_after'])
            if request.query_params.get('created_before'):
                queryset = queryset.filter(created_at__date__lte=request.query_params['created_before'])
        return Response({'success': True, 'data': ReportSerializer(queryset[:100], many=True).data})

    def post(self, request):
        serializer = ReportCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report, created = ModerationService.create_report(reporter=request.user, **serializer.validated_data)
        return Response({'success': True, 'created': created, 'data': ReportSerializer(report).data}, status=201 if created else 200)


class ReportDetailView(APIView):
    permission_classes = [IsActiveAccount]

    def get_object(self, request, public_id):
        queryset = Report.objects.select_related('reporter', 'assigned_moderator')
        if not has_platform_role(request.user, MODERATOR_GROUPS):
            queryset = queryset.filter(reporter=request.user)
        return get_object_or_404(queryset, public_id=public_id)

    def get(self, request, public_id):
        return Response({'success': True, 'data': ReportSerializer(self.get_object(request, public_id)).data})

    def patch(self, request, public_id):
        if not has_platform_role(request.user, MODERATOR_GROUPS):
            raise PermissionDenied('Platform moderator permission is required.')
        report = self.get_object(request, public_id)
        serializer = ReportUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        assigned = None
        if 'assigned_moderator_id' in data:
            assigned = get_object_or_404(User, public_id=data['assigned_moderator_id']) if data['assigned_moderator_id'] else None
            if assigned and not has_platform_role(assigned, MODERATOR_GROUPS):
                raise ValidationError({'assigned_moderator_id': ['Assignee must be a platform moderator.']})
        if data.get('action'):
            ModerationService.apply_action(
                moderator=request.user, report=report, target_type=report.target_type,
                target_public_id=report.target_public_id, action=data['action'],
                reason=data.get('action_reason', data.get('resolution', '')), ends_at=data.get('ends_at'),
            )
        report = ModerationService.update_report(
            report=report, moderator=request.user, status=data.get('status', report.status),
            resolution=data.get('resolution', report.resolution), assigned_moderator=assigned,
        )
        return Response({'success': True, 'data': ReportSerializer(report).data})


class AdminReportsView(ReportListCreateView):
    """Dedicated queue endpoint; unlike /reports/, never falls back to own reports."""
    permission_classes = [IsPlatformAdmin]


class AccessManagementView(APIView):
    permission_classes = [IsPlatformAdmin]
    # Role revocation is an account-recovery operation. Isolate it from the
    # much broader default per-user API quota so a busy app cannot lock a
    # superowner out of correcting an account role.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'admin_access'
    allowed_roles = {'SportMate Venue Owners', 'SportMate Moderators', 'SportMate Admins', 'SportMate Support'}

    def get(self, request):
        users = User.objects.prefetch_related('groups').order_by('username')[:200]
        data = []
        for user in users:
            roles = list(user.groups.values_list('name', flat=True))
            if user.is_superuser and 'SportMate Admins' not in roles:
                roles.append('SportMate Admins')
            data.append({
                'public_id': str(user.public_id),
                'username': user.username,
                'email': user.email,
                'is_active': user.is_active,
                'account_status': user.account_status,
                'roles': roles,
            })
        return Response({'success': True, 'data': data})

    def patch(self, request, public_id):
        user = get_object_or_404(User, public_id=public_id)
        role = str(request.data.get('role', '')).strip()
        action = str(request.data.get('action', '')).strip().lower()
        if role not in self.allowed_roles or action not in {'grant', 'revoke'}:
            raise ValidationError({'detail': 'Use a supported role and grant or revoke action.'})
        target_is_admin = user.is_superuser or user.groups.filter(name__in=ADMIN_GROUPS).exists()
        target_is_venue_owner = user.groups.filter(name=VENUE_OWNER_GROUP).exists()
        target_is_player = PlayerProfile.objects.filter(user=user).exists()
        if action == 'grant' and role == 'SportMate Admins' and (target_is_player or target_is_venue_owner):
            raise ValidationError({
                'role': ['Player and venue-owner accounts cannot be promoted to administrator. Use a separate admin account.']
            })
        if action == 'grant' and role == VENUE_OWNER_GROUP and target_is_admin:
            raise ValidationError({
                'role': ['Administrator accounts cannot be assigned venue-owner access. Use a separate venue-owner account.']
            })
        group, _ = Group.objects.get_or_create(name=role)
        (user.groups.add if action == 'grant' else user.groups.remove)(group)
        RoleAuditLog.objects.create(actor=request.user, target_user=user, role=role, action=action, reason=str(request.data.get('reason', '')).strip())
        return Response({'success': True, 'roles': list(user.groups.values_list('name', flat=True))})


class VenueOwnerApplicationReviewView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        items = VenueOwnerApplication.objects.select_related('applicant').prefetch_related(
            'applicant__owned_venues__source_ground'
        ).order_by('-created_at')
        return Response({'success': True, 'data': [{
            'public_id': str(item.public_id),
            'business_name': item.business_name,
            'phone': item.phone,
            'city': item.city,
            'address': item.address,
            'status': item.status,
            'applicant': item.applicant.username,
            'applicant_name': item.applicant.full_name,
            'applicant_email': item.applicant.email,
            'applicant_phone': item.applicant.phone,
            'created_at': item.created_at,
            'review_note': item.review_note,
            'grounds': [{
                'name': venue.source_ground.name if venue.source_ground_id else venue.name,
                'venue_name': venue.name,
                'city': venue.city,
                'address': venue.address,
            } for venue in item.applicant.owned_venues.all()],
        } for item in items]})

    def patch(self, request, public_id):
        item = get_object_or_404(VenueOwnerApplication, public_id=public_id)
        status = request.data.get('status')
        if status not in {VenueOwnerApplication.Status.APPROVED, VenueOwnerApplication.Status.REJECTED}:
            raise ValidationError({'status': ['Use approved or rejected.']})
        if item.status != VenueOwnerApplication.Status.PENDING:
            raise ValidationError({'status': ['Only pending applications can be reviewed.']})

        review_note = str(request.data.get('review_note', '')).strip()
        if len(review_note) > 500:
            raise ValidationError({'review_note': ['Use 500 characters or fewer.']})
        applicant = item.applicant
        applicant_name = applicant.full_name.strip() or applicant.username
        decision = 'approved' if status == VenueOwnerApplication.Status.APPROVED else 'rejected'
        default_message = (
            'Your venue-owner application has been approved. You can now sign in using Venue login to manage your venue.'
            if status == VenueOwnerApplication.Status.APPROVED else
            'Your venue-owner application was not approved. The associated SportMate account has been removed. You may register again with corrected application details.'
        )
        email_body = (
            f'Hello {applicant_name},\n\n'
            f'Your SportMate venue-owner application for "{item.business_name}" has been {decision}.\n\n'
            f'{default_message}\n\n'
            + (f'Message from the SportMate admin team:\n{review_note}\n\n' if review_note else '')
            + 'Thank you,\nSportMate Admin Team'
        )

        with transaction.atomic():
            if status == VenueOwnerApplication.Status.APPROVED:
                item.status = status
                item.reviewed_by = request.user
                item.review_note = review_note
                item.save(update_fields=['status', 'reviewed_by', 'review_note', 'updated_at'])
                group, _ = Group.objects.get_or_create(name=VENUE_OWNER_GROUP)
                applicant.groups.add(group)
                RoleAuditLog.objects.create(
                    actor=request.user,
                    target_user=applicant,
                    role=VENUE_OWNER_GROUP,
                    action='grant',
                    reason=review_note or 'Venue-owner application approved.',
                )
            else:
                # Delete the rejected applicant. The application is removed by
                # its CASCADE relationship to the applicant account.
                applicant.delete()

            sent_count = send_mail(
                subject=f'SportMate venue-owner application {decision}',
                message=email_body,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[applicant.email],
                fail_silently=False,
            )
            if sent_count != 1:
                raise APIException('The decision email could not be sent. No account or application changes were made.')

        return Response({
            'success': True,
            'status': status,
            'email_sent': True,
            'account_deleted': status == VenueOwnerApplication.Status.REJECTED,
        })


class AdminDashboardView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response({'success': True, 'data': {
            'active_users': User.objects.filter(is_active=True, account_status=User.AccountStatus.ACTIVE).count(),
            'players': PlayerProfile.objects.count(),
            'venue_owners': User.objects.filter(groups__name=VENUE_OWNER_GROUP).distinct().count(),
            'pending_venue_owner_applications': VenueOwnerApplication.objects.filter(status=VenueOwnerApplication.Status.PENDING).count(),
            'new_users': User.objects.filter(date_joined__date=timezone.localdate()).count(),
            'games': Game.objects.count(), 'bookings': CourtBooking.objects.count(),
            'communities': Community.objects.filter(is_active=True).count(), 'events': Event.objects.count(),
            'reports_pending': Report.objects.filter(status=Report.Status.PENDING).count(),
            'reports_under_review': Report.objects.filter(status=Report.Status.UNDER_REVIEW).count(),
            'suspended_users': User.objects.filter(account_status__in=[User.AccountStatus.SUSPENDED, User.AccountStatus.DEACTIVATED]).count(),
            'venues': Venue.objects.count(), 'active_venues': Venue.objects.filter(active=True).count(),
            'pending_venues': Venue.objects.filter(active=False).count(),
        }})


class AdminUsersView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        queryset = User.objects.all().order_by('-date_joined')
        if request.query_params.get('status'):
            queryset = queryset.filter(account_status=request.query_params['status'])
        if request.query_params.get('q'):
            query = request.query_params['q']
            queryset = queryset.filter(Q(username__icontains=query) | Q(email__icontains=query) | Q(full_name__icontains=query))
        if request.query_params.get('joined_after'):
            queryset = queryset.filter(date_joined__date__gte=request.query_params['joined_after'])
        return Response({'success': True, 'data': AdminUserSerializer(queryset[:100], many=True).data})


class AdminModerationActionsView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        queryset = ModerationAction.objects.select_related('moderator', 'report').order_by('-created_at')
        if request.query_params.get('target_type'):
            queryset = queryset.filter(target_type=request.query_params['target_type'])
        if request.query_params.get('moderator'):
            queryset = queryset.filter(moderator__public_id=request.query_params['moderator'])
        if request.query_params.get('action'):
            queryset = queryset.filter(action=request.query_params['action'])
        return Response({'success': True, 'data': ModerationActionSerializer(queryset[:100], many=True).data})


class AdminStatisticsView(APIView):
    permission_classes = [IsPlatformAdmin]

    def get(self, request):
        return Response({'success': True, 'data': {
            'reports_by_status': {row['status']: row['total'] for row in Report.objects.values('status').annotate(total=Count('id'))},
            'actions_by_type': {row['action']: row['total'] for row in ModerationAction.objects.values('action').annotate(total=Count('id'))},
            'games_by_status': {row['status']: row['total'] for row in Game.objects.values('status').annotate(total=Count('id'))},
        }})
