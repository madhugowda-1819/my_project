from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import user_passes_test
from django.core.paginator import Paginator
from django.db.models import Q, Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from my_app.models import User, Venue, Court, College, CollegeResultDispute, PlayerProfile
from my_app.permissions import ADMIN_GROUPS, VENUE_OWNER_GROUP
from my_app_admin_portal.models import Report, RoleAuditLog, ModerationAction
from my_app_venue_portal.models import VenueOwnerApplication
from my_app_admin_portal.services.moderation import ModerationService
from my_app.services.colleges import CollegeService
from datetime import date, timedelta
from django.http import FileResponse
from io import BytesIO

def is_admin(user):
    return user.is_authenticated and user.is_active and (user.is_superuser or user.groups.filter(name__in=ADMIN_GROUPS).exists())

admin_required = user_passes_test(is_admin, login_url='/admin-portal/login/')
ADMIN_SESSION_TIMEOUT_SECONDS = 30 * 60

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
            # Keep the timeout scoped to the browser's Admin Portal session;
            # player and venue-owner sessions are not affected.
            request.session.set_expiry(ADMIN_SESSION_TIMEOUT_SECONDS)
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
    role = request.GET.get('role', '')
    if q: qs = qs.filter(Q(email__icontains=q) | Q(username__icontains=q) | Q(full_name__icontains=q) | Q(player_id__icontains=q) | Q(venue_owner_id__icontains=q))
    if status: qs = qs.filter(account_status=status)
    if role == 'player': qs = qs.filter(profile__isnull=False)
    elif role == 'venue_owner': qs = qs.filter(groups__name=VENUE_OWNER_GROUP)
    qs = qs.distinct()
    return page(request, 'admin_portal/users/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), q=q, status=status, role=role, statuses=User.AccountStatus.choices, portal_section='users')

@admin_required
def venue_owners(request):
    qs = User.objects.filter(groups__name=VENUE_OWNER_GROUP).distinct().order_by('-date_joined')
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if q: qs = qs.filter(Q(email__icontains=q) | Q(full_name__icontains=q) | Q(venue_owner_id__icontains=q))
    if status: qs = qs.filter(account_status=status)
    return page(request, 'admin_portal/venue_owners/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), q=q, status=status, statuses=User.AccountStatus.choices, portal_section='venue_owners')

@admin_required
def venues(request):
    qs = Venue.objects.select_related('owner').prefetch_related('courts').order_by('-created_at')
    q = request.GET.get('q', '').strip()
    status = request.GET.get('status', '')
    if q: qs = qs.filter(Q(name__icontains=q) | Q(city__icontains=q) | Q(owner__email__icontains=q))
    if status == 'active': qs = qs.filter(active=True)
    elif status == 'pending': qs = qs.filter(active=False, owner__isnull=False)
    elif status == 'inactive': qs = qs.filter(active=False, owner__isnull=True)
    owners = User.objects.filter(groups__name=VENUE_OWNER_GROUP, is_active=True, account_status=User.AccountStatus.ACTIVE).order_by('email')
    return page(request, 'admin_portal/venues/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), owners=owners, q=q, status=status, portal_section='venues')

@admin_required
@require_POST
def venue_toggle(request, public_id):
    venue = get_object_or_404(Venue, public_id=public_id)
    active = request.POST.get('active') == '1'
    owner_id = request.POST.get('owner_id', '').strip()
    owner = get_object_or_404(User, public_id=owner_id, is_active=True, account_status=User.AccountStatus.ACTIVE) if owner_id else None
    previous_owner = venue.owner
    venue.owner = owner
    venue.active = active
    venue.save(update_fields=['owner', 'active', 'updated_at'])
    RoleAuditLog.objects.create(actor=request.user, target_user=owner or previous_owner, role='venue_management', action='assign' if owner_id else ('approve' if active else 'reject'), reason=request.POST.get('reason','Web admin action')[:500])
    messages.success(request, 'Venue status updated.')
    return redirect('admin_portal:venues')

@admin_required
def reports(request):
    qs = Report.objects.select_related('reporter','assigned_moderator').order_by('-created_at')
    if request.GET.get('status'): qs = qs.filter(status=request.GET['status'])
    return page(request, 'admin_portal/reports/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), portal_section='reports')

@admin_required
@require_POST
def report_action(request, public_id):
    report = get_object_or_404(Report, public_id=public_id)
    status = request.POST.get('status', report.status)
    resolution = request.POST.get('resolution', '').strip()
    action = request.POST.get('action', '').strip()
    if action:
        ModerationService.apply_action(moderator=request.user, report=report, target_type=report.target_type, target_public_id=report.target_public_id, action=action, reason=request.POST.get('action_reason', '').strip())
    ModerationService.update_report(report=report, moderator=request.user, status=status, resolution=resolution or report.resolution, assigned_moderator=request.user)
    messages.success(request, 'Report updated and recorded in moderation history.')
    return redirect('admin_portal:reports')

@admin_required
def colleges(request):
    qs = College.objects.select_related('submitted_by','reviewed_by').order_by('-created_at')
    return page(request, 'admin_portal/colleges/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), portal_section='colleges')

@admin_required
def disputes(request):
    qs = CollegeResultDispute.objects.select_related('raised_by','resolved_by','match').order_by('-created_at')
    return page(request, 'admin_portal/disputes/list.html', rows=Paginator(qs, 20).get_page(request.GET.get('page')), portal_section='disputes')

@admin_required
@require_POST
def dispute_action(request, public_id):
    dispute = get_object_or_404(CollegeResultDispute, public_id=public_id)
    resolution = request.POST.get('resolution', '').strip()
    if not resolution:
        messages.error(request, 'A resolution note is required.')
        return redirect('admin_portal:disputes')
    dismiss = request.POST.get('decision') == 'dismiss'
    CollegeService.resolve_dispute(dispute=dispute, actor=request.user, resolution=resolution, dismiss=dismiss)
    RoleAuditLog.objects.create(actor=request.user, target_user=dispute.raised_by, role='college_result_dispute', action=dispute.status, reason=resolution[:500])
    messages.success(request, 'Dispute decision saved and audited.')
    return redirect('admin_portal:disputes')

@admin_required
def audit(request):
    qs = RoleAuditLog.objects.select_related('actor','target_user').order_by('-created_at')
    moderation = ModerationAction.objects.select_related('moderator','report').order_by('-created_at')[:50]
    return page(request, 'admin_portal/audit.html', rows=Paginator(qs, 30).get_page(request.GET.get('page')), moderation=moderation, portal_section='audit')

@admin_required
def audit_pdf(request):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    today = timezone.localdate()
    preset = request.GET.get('range', 'today')
    if preset == 'today': start = today
    elif preset == '7d': start = today - timedelta(days=6)
    elif preset == '30d': start = today - timedelta(days=29)
    elif preset == '1m': start = today - timedelta(days=30)
    elif preset == '6m': start = today - timedelta(days=182)
    elif preset == '1y': start = today - timedelta(days=364)
    elif preset == 'custom':
        try:
            start = date.fromisoformat(request.GET.get('from', ''))
            today = date.fromisoformat(request.GET.get('to', ''))
        except ValueError:
            return HttpResponse('Invalid date range. Use YYYY-MM-DD.', status=400)
    else: start = today
    end = today
    audits = RoleAuditLog.objects.filter(created_at__date__range=(start, end)).select_related('actor','target_user').order_by('-created_at')
    actions = ModerationAction.objects.filter(created_at__date__range=(start, end)).select_related('moderator').order_by('-created_at')
    output = BytesIO()
    doc = SimpleDocTemplate(output, pagesize=landscape(A4), rightMargin=12*mm, leftMargin=12*mm, topMargin=12*mm, bottomMargin=12*mm)
    styles = getSampleStyleSheet(); story = [Paragraph('SportMate Admin Activity Report', styles['Title']), Paragraph(f'Period: {start} to {end}', styles['Normal']), Spacer(1, 8)]
    data = [['Time', 'Actor', 'Category / action', 'Target', 'Reason']]
    for item in audits:
        data.append([item.created_at.strftime('%d %b %Y %H:%M'), item.actor.email if item.actor_id else 'System', f'{item.role} / {item.action}', item.target_user.email if item.target_user_id else '—', item.reason or '—'])
    for item in actions:
        data.append([item.created_at.strftime('%d %b %Y %H:%M'), item.moderator.email, f'Moderation / {item.action}', f'{item.target_type} / {item.target_public_id}', item.reason or '—'])
    if len(data) == 1: data.append(['—', '—', 'No activity', '—', 'No records for this period'])
    table = Table(data, repeatRows=1, colWidths=[35*mm, 45*mm, 55*mm, 65*mm, 75*mm])
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#101D3C')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),.25,colors.HexColor('#D7E3F4')),('FONTSIZE',(0,0),(-1,-1),8),('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white, colors.HexColor('#F4F9FF')])]))
    story.append(table); doc.build(story); output.seek(0)
    return FileResponse(output, as_attachment=True, filename=f'sportmate-admin-report-{start}-{end}.pdf', content_type='application/pdf')

# Structured audit export implementation. Defined after the legacy compact
# export above so the route uses this richer report without changing callers.
@admin_required
def audit_pdf(request):
    from uuid import uuid4
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    today = timezone.localdate(); preset = request.GET.get('range', 'today')
    starts = {'today': 0, '7d': 6, '30d': 29, '1m': 30, '6m': 182, '1y': 364}
    if preset == 'custom':
        try: start = date.fromisoformat(request.GET.get('from', '')); end = date.fromisoformat(request.GET.get('to', ''))
        except ValueError: return HttpResponse('Invalid date range. Use YYYY-MM-DD.', status=400)
    else: start, end = today - timedelta(days=starts.get(preset, 0)), today
    if end < start or (end - start).days > 366: return HttpResponse('Invalid or excessive date range.', status=400)
    audits = list(RoleAuditLog.objects.filter(created_at__date__range=(start, end)).select_related('actor','target_user').order_by('-created_at')[:5000])
    actions = list(ModerationAction.objects.filter(created_at__date__range=(start, end)).select_related('moderator').order_by('-created_at')[:5000])
    report_id = f'SM-AUD-{uuid4().hex[:10].upper()}'
    RoleAuditLog.objects.create(actor=request.user, role='audit_export', action='export', reason=f'{start} to {end}')
    styles = getSampleStyleSheet(); title = ParagraphStyle('ReportTitle', parent=styles['Title'], textColor=colors.HexColor('#101D3C'), alignment=TA_CENTER, fontSize=18, spaceAfter=8)
    heading = ParagraphStyle('SectionHeading', parent=styles['Heading2'], textColor=colors.HexColor('#0966F8'), fontSize=12, spaceBefore=12, spaceAfter=6)
    body = ParagraphStyle('Body', parent=styles['BodyText'], fontSize=8, leading=10)
    def P(value, style=body): return Paragraph(str(value or 'Not recorded').replace('&','&amp;').replace('<','&lt;').replace('>','&gt;'), style)
    def footer(canvas, doc):
        canvas.saveState(); canvas.setFont('Helvetica', 8); canvas.setFillColor(colors.HexColor('#657899')); canvas.drawString(15*mm, 9*mm, 'SportMate · Confidential administrative record'); canvas.drawRightString(195*mm, 9*mm, f'Page {doc.page}'); canvas.restoreState()
    output = BytesIO(); doc = SimpleDocTemplate(output, pagesize=A4, rightMargin=15*mm, leftMargin=15*mm, topMargin=14*mm, bottomMargin=16*mm)
    story = [P('SPORTMATE – ADMIN AUDIT LOG REPORT', title), P('Administrative Audit Log', ParagraphStyle('sub', parent=body, alignment=TA_CENTER, fontSize=10)), Spacer(1, 8)]
    metadata = [['Report ID', report_id], ['Report Date', str(today)], ['Date Range', f'{start} to {end}'], ['Generated By', request.user.email], ['Generated At', timezone.localtime().strftime('%d %b %Y %H:%M %Z')], ['Report Type', 'Administrative Audit Log'], ['Total Records', str(len(audits) + len(actions))]]
    story += [P('1. REPORT INFORMATION', heading), Table([[P(k), P(v)] for k,v in metadata], colWidths=[42*mm, 135*mm], style=TableStyle([('BACKGROUND',(0,0),(0,-1),colors.HexColor('#EAF4FF')),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#D7E3F4')),('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7)]))]
    successful = len(audits) + len(actions); pending = sum(1 for x in audits if 'pending' in x.action.lower())
    summary = [['Total Audit Events', str(successful)], ['Successful Actions', str(successful)], ['Failed Actions', 'Not tracked'], ['Pending Reviews', str(pending) if pending else '0']]
    story += [P('2. AUDIT SUMMARY', heading), Table([[P(k), P(v)] for k,v in summary], colWidths=[80*mm, 30*mm], style=TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#F4F9FF')),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#D7E3F4'))]))]
    rows = [[P('Date / Time'), P('Administrator'), P('Action'), P('Affected Record'), P('Result'), P('Source IP'), P('Event Reference')]]
    for x in audits: rows.append([P(timezone.localtime(x.created_at).strftime('%d %b %Y %H:%M')), P(x.actor.email if x.actor_id else 'System'), P(f'{x.role} / {x.action}'), P(x.target_user.email if x.target_user_id else 'Not recorded'), P('Recorded'), P('Not recorded'), P(str(x.public_id))])
    for x in actions: rows.append([P(timezone.localtime(x.created_at).strftime('%d %b %Y %H:%M')), P(x.moderator.email), P(f'Moderation / {x.action}'), P(f'{x.target_type} / {x.target_public_id}'), P('Recorded'), P('Not recorded'), P(str(x.public_id))])
    if len(rows) == 1: rows.append([P('—'),P('—'),P('No matching records'),P('—'),P('—'),P('Not recorded'),P('—')])
    story += [P('3. DETAILED AUDIT LOG', heading), Table(rows, repeatRows=1, colWidths=[25*mm,31*mm,33*mm,37*mm,20*mm,20*mm,27*mm], style=TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#101D3C')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),.25,colors.HexColor('#D7E3F4')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white,colors.HexColor('#F4F9FF')]),('VALIGN',(0,0),(-1,-1),'TOP'),('FONTSIZE',(0,0),(-1,-1),7)]))]
    security = [['Review item', 'Evidence in selected period', 'Review state']]
    checks = [('Privileged role changes', any('role' in x.role.lower() for x in audits)), ('User suspensions', any('suspend' in x.action.lower() for x in actions)), ('Account reactivations', any('restore' in x.action.lower() for x in actions)), ('Venue approval/rejection', any('venue' in x.role.lower() for x in audits)), ('Moderation decisions', bool(actions)), ('Unauthorized attempts', False)]
    for label, found in checks: security.append([label, 'Recorded activity found' if found else 'No matching record', 'Pending Review'])
    story += [P('4. SECURITY REVIEW', heading), Table([[P(a),P(b),P(c)] for a,b,c in security], colWidths=[65*mm,65*mm,45*mm], style=TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#101D3C')),('TEXTCOLOR',(0,0),(-1,0),colors.white),('GRID',(0,0),(-1,-1),.25,colors.HexColor('#D7E3F4'))]))]
    story += [P('5. AUDIT EVENT DETAILS', heading), P('Detailed before/after state is included only where stored by the moderation system. Source IP, supporting evidence, and unavailable fields are reported as Not recorded. Passwords, tokens, session secrets, and credentials are never exported.'), P('6. SIGN-OFF', heading), Table([[P('Prepared By'),P(request.user.email)], [P('Reviewed By'),P('Pending Review')], [P('Approval'),P('Not Approved / Pending Approval')]], colWidths=[42*mm,135*mm], style=TableStyle([('BACKGROUND',(0,0),(0,-1),colors.HexColor('#EAF4FF')),('GRID',(0,0),(-1,-1),.3,colors.HexColor('#D7E3F4'))]))]
    doc.build(story, onFirstPage=footer, onLaterPages=footer); output.seek(0)
    return FileResponse(output, as_attachment=True, filename=f'SportMate_Admin_Audit_Logs_{start}.pdf', content_type='application/pdf')
