from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from my_app.models import User, Venue, Court, College, CollegeResultDispute, PlayerProfile
from my_app.permissions import ADMIN_GROUPS, VENUE_OWNER_GROUP
from my_app_admin_portal.models import Report, RoleAuditLog, ModerationAction
from my_app_venue_portal.models import VenueOwnerApplication

def is_admin(user):
    return user.is_authenticated and user.is_active and (user.is_superuser or user.groups.filter(name__in=ADMIN_GROUPS).exists())

admin_required = user_passes_test(is_admin, login_url='/admin-portal/login/')

@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.user.is_authenticated and is_admin(request.user):
        return redirect('admin_portal:dashboard')
    error = None
    if request.method == 'POST':
        identity = request.POST.get('identity', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=identity, password=password)
        # SportMate uses email as USERNAME_FIELD, but older administrator
        # accounts may still remember their username. Resolve that username
        # to the canonical email before authenticating.
        if user is None and identity:
            account = User.objects.filter(username__iexact=identity).first()
            if account:
                user = authenticate(request, username=account.email, password=password)
        if user and is_admin(user):
            login(request, user)
            return redirect(request.GET.get('next') or 'admin_portal:dashboard')
        error = 'Use an authorized SportMate administrator account.'
    return render(request, 'admin_portal/login.html', {'error': error})

@require_POST
def logout_view(request):
    logout(request)
    return redirect('admin_portal:login')

def page(request, template, **context):
    context['portal_section'] = context.get('portal_section', '')
    return render(request, template, context)

@admin_required
def dashboard(request):
    metrics = {
        'users': User.objects.count(), 'players': PlayerProfile.objects.count(),
        'venue_owners': User.objects.filter(groups__name=VENUE_OWNER_GROUP).distinct().count(),
        'venues': Venue.objects.count(), 'courts': Court.objects.count(),
        'bookings': __import__('my_app.models', fromlist=['CourtBooking']).CourtBooking.objects.count(),
        'pending_reports': Report.objects.filter(status=Report.Status.PENDING).count(),
        'open_disputes': CollegeResultDispute.objects.filter(status=CollegeResultDispute.Status.OPEN).count(),
    }
    return page(request, 'admin_portal/dashboard.html', metrics=metrics, audits=RoleAuditLog.objects.select_related('actor','target_user')[:8], portal_section='dashboard')

@admin_required
def users(request):
    qs = User.objects.prefetch_related('groups').order_by('-date_joined')
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if q: qs = qs.filter(Q(email__icontains=q) | Q(username__icontains=q) | Q(full_name__icontains=q) | Q(player_id__icontains=q) | Q(venue_owner_id__icontains=q))
    if status: qs = qs.filter(account_status=status)
    return page(request, 'admin_portal/users/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), q=q, status=status, statuses=User.AccountStatus.choices, portal_section='users')

@admin_required
def venue_owners(request):
    qs = User.objects.filter(groups__name=VENUE_OWNER_GROUP).distinct().order_by('-date_joined')
    q = request.GET.get('q', '').strip()
    if q: qs = qs.filter(Q(email__icontains=q) | Q(full_name__icontains=q) | Q(venue_owner_id__icontains=q))
    return page(request, 'admin_portal/venue_owners/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), q=q, portal_section='venue_owners')

@admin_required
def venues(request):
    qs = Venue.objects.select_related('owner').prefetch_related('courts').order_by('-created_at')
    q = request.GET.get('q', '').strip()
    if q: qs = qs.filter(Q(name__icontains=q) | Q(city__icontains=q) | Q(owner__email__icontains=q))
    return page(request, 'admin_portal/venues/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), q=q, portal_section='venues')

@admin_required
@require_POST
def venue_toggle(request, public_id):
    venue = get_object_or_404(Venue, public_id=public_id)
    active = request.POST.get('active') == '1'
    venue.active = active; venue.save(update_fields=['active', 'updated_at'])
    RoleAuditLog.objects.create(actor=request.user, target_user=venue.owner, role='venue_management', action='approve' if active else 'reject', reason=request.POST.get('reason','Web admin action')[:500])
    messages.success(request, 'Venue status updated.')
    return redirect('admin_portal:venues')

@admin_required
def reports(request):
    qs = Report.objects.select_related('reporter','assigned_moderator').order_by('-created_at')
    if request.GET.get('status'): qs = qs.filter(status=request.GET['status'])
    return page(request, 'admin_portal/reports/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), portal_section='reports')

@admin_required
def colleges(request):
    qs = College.objects.select_related('submitted_by','reviewed_by').order_by('-created_at')
    return page(request, 'admin_portal/colleges/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), portal_section='colleges')

@admin_required
def disputes(request):
    qs = CollegeResultDispute.objects.select_related('raised_by','resolved_by','match').order_by('-created_at')
    return page(request, 'admin_portal/disputes/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), portal_section='disputes')

@admin_required
def audit(request):
    qs = RoleAuditLog.objects.select_related('actor','target_user').order_by('-created_at')
    return page(request, 'admin_portal/audit.html', rows=Paginator(qs, 30).get_page(request.GET.get('page')), portal_section='audit')
