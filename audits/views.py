from io import BytesIO
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from xml.sax.saxutils import escape
from uuid import uuid4

import fitz

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.db import IntegrityError, transaction
from django.http import FileResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.text import slugify
from rest_framework import filters, permissions, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.exceptions import ValidationError
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from rest_framework_simplejwt.views import TokenObtainPairView
from rest_framework_simplejwt.exceptions import AuthenticationFailed, InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from .catalog import ENGAGEMENT_PACKAGES
from .models import Audit, AuditModule, Evidence, Finding
from .serializers import (
    AuditModuleSerializer,
    AuditSerializer,
    EmailTokenObtainPairSerializer,
    EvidenceSerializer,
    FindingSerializer,
    RegisterSerializer,
    SharedAuditSerializer,
)


class EmailTokenObtainPairView(TokenObtainPairView):
    serializer_class = EmailTokenObtainPairSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tokens = dict(serializer.validated_data)
        refresh_token = tokens.pop("refresh")
        update_last_login(None, serializer.user)
        response = Response(tokens, status=status.HTTP_200_OK)
        set_refresh_cookie(response, refresh_token)
        return response


def set_refresh_cookie(response, token):
    response.set_cookie(
        key=settings.AUTH_REFRESH_COOKIE,
        value=token,
        max_age=int(settings.SIMPLE_JWT["REFRESH_TOKEN_LIFETIME"].total_seconds()),
        httponly=True,
        secure=settings.AUTH_REFRESH_COOKIE_SECURE,
        samesite=settings.AUTH_REFRESH_COOKIE_SAMESITE,
        path="/api/auth/",
    )


def clear_refresh_cookie(response):
    response.delete_cookie(
        key=settings.AUTH_REFRESH_COOKIE,
        path="/api/auth/",
        samesite=settings.AUTH_REFRESH_COOKIE_SAMESITE,
    )


class RefreshCookieView(GenericAPIView):
    serializer_class = TokenRefreshSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE)
        if not refresh_token:
            response = Response({"detail": "Refresh session is missing. Please sign in again."}, status=status.HTTP_401_UNAUTHORIZED)
            clear_refresh_cookie(response)
            return response
        serializer = self.get_serializer(data={"refresh": refresh_token})
        try:
            serializer.is_valid(raise_exception=True)
        except (AuthenticationFailed, InvalidToken, TokenError):
            response = Response({"detail": "Refresh session is invalid or expired. Please sign in again."}, status=status.HTTP_401_UNAUTHORIZED)
            clear_refresh_cookie(response)
            return response
        tokens = dict(serializer.validated_data)
        rotated_refresh = tokens.pop("refresh", None)
        response = Response(tokens, status=status.HTTP_200_OK)
        if rotated_refresh:
            set_refresh_cookie(response, rotated_refresh)
        return response


class LogoutView(GenericAPIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request, *args, **kwargs):
        refresh_token = request.COOKIES.get(settings.AUTH_REFRESH_COOKIE)
        if refresh_token:
            try:
                RefreshToken(refresh_token).blacklist()
            except (AuthenticationFailed, InvalidToken, TokenError):
                pass
        response = Response(status=status.HTTP_204_NO_CONTENT)
        clear_refresh_cookie(response)
        return response


class RegisterView(GenericAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                result = serializer.save()
        except IntegrityError:
            return Response(
                {"email": ["An account with this email already exists. Sign in instead."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        refresh_token = result.pop("refresh")
        response = Response(result, status=status.HTTP_201_CREATED)
        set_refresh_cookie(response, refresh_token)
        return response


class OwnedAuditQuerysetMixin:
    def get_queryset(self):
        queryset = super().get_queryset()
        user = self.request.user
        if user.is_staff:
            return queryset
        return queryset.filter(owner=user)


def audit_accepts_edits(audit):
    return audit.status in {Audit.Status.IN_PROGRESS, Audit.Status.NEEDS_CHANGES}


class AuditViewSet(OwnedAuditQuerysetMixin, viewsets.ModelViewSet):
    serializer_class = AuditSerializer
    queryset = Audit.objects.all()
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ["client", "audit_type", "goal"]
    ordering_fields = ["created_at", "updated_at", "client", "status"]

    def get_queryset(self):
        return super().get_queryset().select_related("owner").prefetch_related("modules")

    def perform_update(self, serializer):
        if "status" in self.request.data:
            raise ValidationError({"status": "Use the submit-for-review workflow action to change audit status."})
        editable_post_approval_fields = {"delivery_data", "commercial_data"}
        approved_follow_up_update = (
            serializer.instance.status == Audit.Status.APPROVED
            and bool(self.request.data)
            and set(self.request.data.keys()).issubset(editable_post_approval_fields)
        )
        if not audit_accepts_edits(serializer.instance) and not approved_follow_up_update:
            raise ValidationError({"detail": "Audits cannot be edited while in review or after approval."})
        serializer.save()

    def perform_destroy(self, instance):
        if instance.status in {Audit.Status.IN_REVIEW, Audit.Status.APPROVED}:
            raise ValidationError({"detail": "Audits in review or approved cannot be deleted. Return an audit for changes before deleting it."})
        attachments = [
            (evidence.attachment.storage, evidence.attachment.name)
            for evidence in instance.evidence.exclude(attachment="")
            if evidence.attachment
        ]
        with transaction.atomic():
            instance.delete()
            for storage, name in attachments:
                transaction.on_commit(lambda storage=storage, name=name: storage.delete(name))

    @action(detail=True, methods=["post"])
    def submit_for_review(self, request, pk=None):
        audit = self.get_object()
        if audit.status not in {Audit.Status.IN_PROGRESS, Audit.Status.NEEDS_CHANGES}:
            return Response(
                {"detail": "Only an in-progress audit or an audit returned for changes can be submitted."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        missing = audit.modules.filter(required=True).exclude(status=AuditModule.Status.COMPLETE)
        if missing.exists():
            return Response(
                {"detail": "Complete every required module before submitting for review.", "missing_modules": list(missing.values_list("title", flat=True))},
                status=status.HTTP_400_BAD_REQUEST,
            )
        audit.status = Audit.Status.IN_REVIEW
        audit.review_feedback = ""
        audit.quality_checks = {}
        audit.save(update_fields=["status", "review_feedback", "quality_checks", "updated_at"])
        return Response(self.get_serializer(audit).data)

    @action(detail=True, methods=["post"])
    def quality_checks(self, request, pk=None):
        if not request.user.is_staff:
            return Response({"detail": "Only a quality reviewer can record the review checklist."}, status=status.HTTP_403_FORBIDDEN)
        audit = self.get_object()
        if audit.status != Audit.Status.IN_REVIEW:
            return Response({"detail": "Quality checks are available while an audit is in review."}, status=status.HTTP_400_BAD_REQUEST)
        checks = request.data.get("checks")
        required_checks = {
            "completeness",
            "technical_accuracy",
            "client_relevance",
            "clarity",
            "design",
            "methodology",
        }
        if not isinstance(checks, dict) or set(checks) != required_checks or any(type(value) is not bool for value in checks.values()):
            return Response(
                {"checks": "Provide a true or false value for each of the six quality review checks."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        notes = request.data.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 4000:
            return Response({"notes": "Reviewer notes must be 4,000 characters or fewer."}, status=status.HTTP_400_BAD_REQUEST)
        audit.quality_checks = {
            **checks,
            "reviewer_notes": notes,
            "checked_by": request.user.get_full_name() or request.user.email or request.user.get_username(),
            "checked_at": timezone.now().isoformat(),
        }
        audit.save(update_fields=["quality_checks", "updated_at"])
        return Response(self.get_serializer(audit).data)

    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        if not request.user.is_staff:
            return Response({"detail": "A quality reviewer must approve this audit."}, status=status.HTTP_403_FORBIDDEN)
        audit = self.get_object()
        if audit.status != Audit.Status.IN_REVIEW:
            return Response({"detail": "Only audits in review can be approved."}, status=status.HTTP_400_BAD_REQUEST)
        required_checks = {
            "completeness",
            "technical_accuracy",
            "client_relevance",
            "clarity",
            "design",
            "methodology",
        }
        if not required_checks.issubset(audit.quality_checks) or not all(audit.quality_checks[key] is True for key in required_checks):
            return Response(
                {"detail": "Complete and pass all six quality checks before approving this audit."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        audit.status = Audit.Status.APPROVED
        audit.approved_by = request.user
        audit.approved_at = timezone.now()
        audit.save(update_fields=["status", "approved_by", "approved_at", "updated_at"])
        return Response(self.get_serializer(audit).data)

    @action(detail=True, methods=["post"])
    def close_deal(self, request, pk=None):
        audit = self.get_object()
        if audit.status != Audit.Status.APPROVED:
            return Response({"detail": "The audit must be approved before closing a follow-up engagement."}, status=status.HTTP_400_BAD_REQUEST)
        commercial_data = audit.commercial_data or {}
        package = ENGAGEMENT_PACKAGES.get(commercial_data.get("package_id"))
        if package is None:
            return Response({"detail": "Select an implementation package before marking the deal won."}, status=status.HTTP_400_BAD_REQUEST)
        commercial_data.update(package)
        commercial_data.update({"status": "won", "closed_at": timezone.now().isoformat()})
        audit.commercial_data = commercial_data
        audit.save(update_fields=["commercial_data", "updated_at"])
        return Response(self.get_serializer(audit).data)

    @action(detail=True, methods=["get"])
    def report(self, request, pk=None):
        audit = self.get_object()
        if audit.status != Audit.Status.APPROVED:
            return Response(
                {"detail": "The audit report is available after quality approval."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return build_audit_report(audit)

    @action(detail=True, methods=["post"])
    def request_changes(self, request, pk=None):
        if not request.user.is_staff:
            return Response({"detail": "A quality reviewer must request changes."}, status=status.HTTP_403_FORBIDDEN)
        audit = self.get_object()
        if audit.status != Audit.Status.IN_REVIEW:
            return Response({"detail": "Only audits in review can be returned for changes."}, status=status.HTTP_400_BAD_REQUEST)
        feedback = request.data.get("feedback", "").strip()
        if not feedback:
            return Response({"feedback": ["Explain what needs to change before returning this audit."]}, status=status.HTTP_400_BAD_REQUEST)
        audit.status = Audit.Status.NEEDS_CHANGES
        audit.review_feedback = feedback
        audit.quality_checks = {}
        audit.approved_by = None
        audit.approved_at = None
        audit.save(update_fields=["status", "review_feedback", "quality_checks", "approved_by", "approved_at", "updated_at"])
        return Response(self.get_serializer(audit).data)

    @action(detail=True, methods=["post"])
    def share(self, request, pk=None):
        audit = self.get_object()
        if audit.status != Audit.Status.APPROVED:
            return Response({"detail": "Approve the audit before creating a client share link."}, status=status.HTTP_400_BAD_REQUEST)
        if audit.share_token is None:
            audit.share_token = uuid4()
            audit.shared_at = timezone.now()
            audit.save(update_fields=["share_token", "shared_at", "updated_at"])
        return Response({
            "token": audit.share_token,
            "shared_at": audit.shared_at,
            "message": "The approved audit is ready for secure client sharing.",
        })

    @action(detail=True, methods=["post"])
    def revoke_share(self, request, pk=None):
        audit = self.get_object()
        if audit.share_token is None:
            return Response({"detail": "This audit does not have an active share link."}, status=status.HTTP_400_BAD_REQUEST)
        audit.share_token = None
        audit.shared_at = None
        audit.save(update_fields=["share_token", "shared_at", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"])
    def review_queue(self, request):
        queryset = self.get_queryset().filter(status=Audit.Status.IN_REVIEW)
        return Response(self.get_serializer(queryset, many=True).data)


class ModuleViewSet(OwnedAuditQuerysetMixin, viewsets.ModelViewSet):
    serializer_class = AuditModuleSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_queryset(self):
        queryset = AuditModule.objects.select_related("audit").prefetch_related("evidence")
        queryset = queryset.filter(audit__owner=self.request.user)
        if self.request.user.is_staff:
            queryset = AuditModule.objects.select_related("audit").prefetch_related("evidence")
        audit_id = self.request.query_params.get("audit")
        if audit_id:
            queryset = queryset.filter(audit_id=audit_id)
        return queryset

    def perform_update(self, serializer):
        if not audit_accepts_edits(serializer.instance.audit):
            raise ValidationError({"detail": "Modules cannot be edited while the audit is in review or approved."})
        serializer.save()


class AuditScopedViewSetMixin:
    def get_queryset(self):
        queryset = super().get_queryset().select_related("audit", "audit__owner")
        if self.request.user.is_staff:
            owned = queryset
        else:
            owned = queryset.filter(audit__owner=self.request.user)
        audit_id = self.request.query_params.get("audit")
        return owned.filter(audit_id=audit_id) if audit_id else owned

    def perform_create(self, serializer):
        audit = serializer.validated_data["audit"]
        if not self.request.user.is_staff and audit.owner_id != self.request.user.id:
            raise ValidationError({"audit": "You do not have access to this audit."})
        if not audit_accepts_edits(audit):
            raise ValidationError({"audit": "Findings and evidence cannot be changed while the audit is in review or approved."})
        serializer.save(**self.get_create_kwargs())

    def perform_update(self, serializer):
        if not audit_accepts_edits(serializer.instance.audit):
            raise ValidationError({"detail": "Findings and evidence cannot be changed while the audit is in review or approved."})
        serializer.save()

    def perform_destroy(self, instance):
        if not audit_accepts_edits(instance.audit):
            raise ValidationError({"detail": "Findings and evidence cannot be changed while the audit is in review or approved."})
        instance.delete()

    def get_create_kwargs(self):
        return {}


class EvidenceViewSet(AuditScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = EvidenceSerializer
    http_method_names = ["get", "post", "delete", "head", "options"]
    queryset = Evidence.objects.all()

    def get_create_kwargs(self):
        return {"added_by": self.request.user}

    @action(detail=True, methods=["get"])
    def download(self, request, pk=None):
        evidence = self.get_object()
        if not evidence.attachment:
            return Response({"detail": "This evidence item has no uploaded file."}, status=status.HTTP_404_NOT_FOUND)
        return FileResponse(
            evidence.attachment.open("rb"),
            as_attachment=True,
            filename=evidence.attachment.name.rsplit("/", 1)[-1],
            content_type="application/octet-stream",
        )


class FindingViewSet(AuditScopedViewSetMixin, viewsets.ModelViewSet):
    serializer_class = FindingSerializer
    queryset = Finding.objects.all()


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def shared_audit(request, token):
    audit = Audit.objects.filter(
        share_token=token,
        status=Audit.Status.APPROVED,
    ).prefetch_related("findings__module").first()
    if audit is None:
        return Response({"detail": "This client audit link is invalid or no longer active."}, status=status.HTTP_404_NOT_FOUND)
    return Response(SharedAuditSerializer(audit).data)


def build_html_audit_report(audit):
    """Render the approved audit as a print-first HTML document.

    The template follows the client report reference design while keeping every
    value scoped to the audit passed by the approved-report endpoint.
    """
    modules = list(audit.modules.all())
    scored_modules = [module for module in modules if module.assessment_score is not None]
    evidence_items = [item for module in modules for item in module.evidence.all()]
    priority_order = {
        Finding.Priority.CRITICAL: 0,
        Finding.Priority.HIGH: 1,
        Finding.Priority.MEDIUM: 2,
        Finding.Priority.LOW: 3,
    }
    findings = sorted(
        audit.findings.select_related("module").all(),
        key=lambda finding: (priority_order[finding.priority], finding.created_at),
    )
    average_score = (
        round(sum(module.assessment_score for module in scored_modules) / len(scored_modules))
        if scored_modules else None
    )
    roadmap = audit.roadmap_data or {}
    raw_kpis = roadmap.get("kpis", [])
    metric_cards = []
    for kpi in raw_kpis[:4]:
        baseline = kpi.get("baseline") or "Not set"
        target = kpi.get("target") or "Not set"
        unit = kpi.get("unit") or ""
        metric_cards.append({
            "name": kpi.get("name") or "Performance KPI",
            "value": f"{baseline}{unit}",
            "target": f"Target: {target}{unit}",
        })
    fallback_metrics = [
        {
            "name": "Overall audit health",
            "value": f"{average_score}/100" if average_score is not None else "Not scored",
            "target": "Target: improvement plan approved",
        },
        {
            "name": "Priority findings",
            "value": str(len(findings)),
            "target": "Target: highest-priority actions resolved",
        },
        {
            "name": "Evidence captured",
            "value": str(len(evidence_items)),
            "target": "Target: decisions supported by evidence",
        },
        {
            "name": "Assessment coverage",
            "value": f"{len(scored_modules)}/{len(modules)}",
            "target": "Target: all required modules reviewed",
        },
    ]
    metric_cards.extend(fallback_metrics[:max(0, 4 - len(metric_cards))])
    approved_at = timezone.localtime(audit.approved_at or audit.updated_at)
    context = {
        "audit": audit,
        "modules": modules,
        "scored_modules": scored_modules,
        "findings": findings,
        "evidence_items": evidence_items,
        "average_score": average_score,
        "roadmap_items": roadmap.get("items", []),
        "kpis": raw_kpis,
        "metric_cards": metric_cards,
        "headline_findings": findings[:3],
        "additional_findings": findings[3:],
        "approved_at": approved_at,
        "owner_name": audit.owner.get_full_name() or audit.owner.email or audit.owner.get_username(),
        "reviewer_name": (
            audit.approved_by.get_full_name() or audit.approved_by.email or audit.approved_by.get_username()
            if audit.approved_by else "Quality reviewer"
        ),
    }
    html = render_to_string("audits/audit_report.html", context)

    # The browser print engine supports the modern CSS used by the supplied
    # reference (grid, gradients, print colours and page-break rules).
    browser_candidates = [
        os.environ.get("AUDIT_REPORT_CHROMIUM_PATH", ""),
        r"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files (x86)\\Google\\Chrome\\Application\\chrome.exe",
        r"C:\\Program Files\\Microsoft\\Edge\\Application\\msedge.exe",
        r"C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
        shutil.which("google-chrome") or "",
        shutil.which("chromium") or "",
        shutil.which("chromium-browser") or "",
    ]
    browser = next((candidate for candidate in browser_candidates if candidate and Path(candidate).is_file()), None)
    if browser:
        with tempfile.TemporaryDirectory(prefix="auditpulse-report-") as temp_dir:
            html_path = Path(temp_dir) / "report.html"
            pdf_path = Path(temp_dir) / "report.pdf"
            html_path.write_text(html, encoding="utf-8")
            result = subprocess.run(
                [
                    browser,
                    "--headless=new",
                    "--disable-gpu",
                    "--no-sandbox",
                    "--print-to-pdf-no-header",
                    f"--print-to-pdf={pdf_path}",
                    html_path.as_uri(),
                ],
                capture_output=True,
                text=True,
                timeout=90,
                check=False,
            )
            if result.returncode == 0 and pdf_path.exists() and pdf_path.stat().st_size > 1024:
                pdf = pdf_path.read_bytes()
                filename = f"{slugify(audit.client) or 'client'}-audit-report.pdf"
                return FileResponse(
                    BytesIO(pdf),
                    as_attachment=True,
                    filename=filename,
                    content_type="application/pdf",
                )

    # Compatibility fallback for environments without Chromium. Production
    # should install Chromium and configure AUDIT_REPORT_CHROMIUM_PATH.
    page_rect = fitz.paper_rect("a4")
    content_rect = fitz.Rect(40, 42, page_rect.width - 40, page_rect.height - 58)
    story = fitz.Story(html=html)
    pdf_buffer = BytesIO()
    writer = fitz.DocumentWriter(pdf_buffer)
    more = 1
    while more:
        device = writer.begin_page(page_rect)
        more, _ = story.place(content_rect)
        story.draw(device)
        writer.end_page()
    writer.close()
    pdf = pdf_buffer.getvalue()
    document = fitz.open(stream=pdf, filetype="pdf")
    page_total = len(document)
    for page_number, page in enumerate(document, start=1):
        page.draw_line(
            fitz.Point(40, page_rect.height - 34),
            fitz.Point(page_rect.width - 40, page_rect.height - 34),
            color=(0.87, 0.89, 0.92),
            width=0.5,
        )
        page.insert_text(
            fitz.Point(40, page_rect.height - 20),
            f"AuditPulse - {audit.client}",
            fontsize=7,
            color=(0.41, 0.46, 0.54),
        )
        page.insert_text(
            fitz.Point(page_rect.width - 92, page_rect.height - 20),
            f"Page {page_number} of {page_total}",
            fontsize=7,
            color=(0.41, 0.46, 0.54),
        )
    pdf = document.tobytes(garbage=4, deflate=True)
    document.close()
    filename = f"{slugify(audit.client) or 'client'}-audit-report.pdf"
    return FileResponse(
        BytesIO(pdf),
        as_attachment=True,
        filename=filename,
        content_type="application/pdf",
    )


def build_audit_report(audit):
    return build_html_audit_report(audit)


def build_legacy_audit_report(audit):
    buffer = BytesIO()
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="FieldnotesTitle", parent=styles["Title"], fontName="Helvetica-Bold",
        fontSize=25, leading=30, textColor=colors.HexColor("#172236"), alignment=TA_CENTER,
        spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="FieldnotesSection", parent=styles["Heading2"], fontName="Helvetica-Bold",
        fontSize=14, leading=18, textColor=colors.HexColor("#172236"), spaceBefore=18, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="FieldnotesBody", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=9, leading=14, textColor=colors.HexColor("#475467"),
    ))
    styles.add(ParagraphStyle(
        name="FieldnotesMeta", parent=styles["BodyText"], fontName="Helvetica",
        fontSize=8, leading=12, textColor=colors.HexColor("#667085"),
    ))
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, rightMargin=20 * mm, leftMargin=20 * mm,
        topMargin=22 * mm, bottomMargin=20 * mm, title=f"{audit.client} - AuditPulse report",
        author="AuditPulse",
    )
    story = [
        Table([[Paragraph("<b>AUDITPULSE</b>", styles["FieldnotesBody"]), Paragraph("CONFIDENTIAL CLIENT REPORT", styles["FieldnotesMeta"])]], colWidths=[85 * mm, 80 * mm], style=TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F7FA")),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#DDE4EE")),
            ("ALIGN", (1, 0), (1, 0), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ])),
        Spacer(1, 18),
        Paragraph("APPROVED DIGITAL AUDIT", styles["FieldnotesMeta"]),
        Spacer(1, 5),
        Paragraph(escape(audit.client), styles["FieldnotesTitle"]),
        Paragraph(escape(audit.audit_type), styles["FieldnotesBody"]),
        Spacer(1, 10),
        HRFlowable(width="100%", thickness=1.2, color=colors.HexColor("#2563EB")),
    ]
    summary = [
        [
            Paragraph("<b>STATUS</b>", styles["FieldnotesMeta"]),
            Paragraph("<b>OWNER</b>", styles["FieldnotesMeta"]),
            Paragraph("<b>REVIEWER</b>", styles["FieldnotesMeta"]),
            Paragraph("<b>APPROVED</b>", styles["FieldnotesMeta"]),
        ],
        [
            Paragraph("Quality approved", styles["FieldnotesBody"]),
            Paragraph(escape(audit.owner.get_full_name() or audit.owner.email or audit.owner.get_username()), styles["FieldnotesBody"]),
            Paragraph(escape(audit.approved_by.get_full_name() or audit.approved_by.email or audit.approved_by.get_username()) if audit.approved_by else "Reviewer", styles["FieldnotesBody"]),
            Paragraph(timezone.localtime(audit.approved_at or audit.updated_at).strftime("%b %d, %Y"), styles["FieldnotesBody"]),
        ],
    ]
    summary_table = Table(summary, colWidths=[38 * mm, 45 * mm, 45 * mm, 42 * mm])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#172236")),
        ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#F8FAFC")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDE4EE")),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE4EE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 9),
        ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.extend([Spacer(1, 10), summary_table])
    if audit.goal:
        story.extend([
            Paragraph("Audit objectives", styles["FieldnotesSection"]),
            Paragraph(escape(audit.goal).replace("\n", "<br/>"), styles["FieldnotesBody"]),
        ])
    modules = list(audit.modules.all())
    scored_modules = [module for module in modules if module.assessment_score is not None]
    evidence_count = sum(module.evidence.count() for module in modules)
    priority_order = {
        Finding.Priority.CRITICAL: 0,
        Finding.Priority.HIGH: 1,
        Finding.Priority.MEDIUM: 2,
        Finding.Priority.LOW: 3,
    }
    findings = sorted(
        audit.findings.select_related("module").all(),
        key=lambda finding: (priority_order[finding.priority], finding.created_at),
    )
    if scored_modules or findings:
        average_score = round(sum(module.assessment_score for module in scored_modules) / len(scored_modules)) if scored_modules else None
        story.append(Paragraph("Executive diagnostic summary", styles["FieldnotesSection"]))
        summary_parts = []
        if average_score is not None:
            summary_parts.append(f"Average health across {len(scored_modules)} scored modules: <b>{average_score}/100</b>.")
        summary_parts.append(f"The audit contains <b>{len(findings)} finding(s)</b> supported by <b>{evidence_count} evidence item(s)</b>.")
        story.append(Paragraph(" ".join(summary_parts), styles["FieldnotesBody"]))
        metric_rows = [[
            Paragraph("<b>HEALTH SCORE</b><br/>" + (f"<font size='16'>{average_score}/100</font>" if average_score is not None else "Not scored"), styles["FieldnotesBody"]),
            Paragraph(f"<b>KEY FINDINGS</b><br/><font size='16'>{len(findings)}</font>", styles["FieldnotesBody"]),
            Paragraph(f"<b>EVIDENCE ITEMS</b><br/><font size='16'>{evidence_count}</font>", styles["FieldnotesBody"]),
        ]]
        metric_table = Table(metric_rows, colWidths=[55 * mm, 55 * mm, 55 * mm])
        metric_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EFF6FF")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFDBFE")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#BFDBFE")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10),
            ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        story.extend([Spacer(1, 10), metric_table])
        for finding in findings[:3]:
            story.extend([
                Spacer(1, 5),
                Paragraph(
                    f"<b>{escape(finding.get_priority_display())} · {escape(finding.title)}:</b> "
                    f"{escape(finding.problem or finding.why_it_matters or finding.recommendation)}",
                    styles["FieldnotesBody"],
                ),
            ])
    if audit.contact_email or audit.website_url or audit.industry:
        profile = [
            f"<b>Website:</b> {escape(audit.website_url)}" if audit.website_url else "",
            f"<b>Primary contact:</b> {escape(audit.contact_email)}" if audit.contact_email else "",
            f"<b>Industry:</b> {escape(audit.industry)}" if audit.industry else "",
            f"<b>Audit category:</b> {escape(audit.audit_category)}" if audit.audit_category else "",
        ]
        story.extend([
            Paragraph("Client profile", styles["FieldnotesSection"]),
            Paragraph("<br/>".join(value for value in profile if value), styles["FieldnotesBody"]),
        ])
    story.append(Paragraph("Audit coverage", styles["FieldnotesSection"]))
    module_rows = [[
        Paragraph("<b>MODULE</b>", styles["FieldnotesMeta"]),
        Paragraph("<b>STATUS</b>", styles["FieldnotesMeta"]),
        Paragraph("<b>EVIDENCE</b>", styles["FieldnotesMeta"]),
    ]]
    for module in modules:
        evidence = list(module.evidence.all())
        module_rows.append([
            Paragraph(escape(module.title), styles["FieldnotesBody"]),
            Paragraph(escape(module.get_status_display()), styles["FieldnotesBody"]),
            Paragraph(str(len(evidence)), styles["FieldnotesBody"]),
        ])
    module_table = Table(module_rows, colWidths=[92 * mm, 40 * mm, 33 * mm], repeatRows=1)
    module_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EAF1FB")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#DDE4EE")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    story.append(module_table)
    if any(module.assessment_score is not None or module.assessment_notes or module.benchmark_notes for module in modules):
        story.append(Paragraph("Assessment & benchmarks", styles["FieldnotesSection"]))
        for module in modules:
            if module.assessment_score is None and not module.assessment_notes and not module.benchmark_notes:
                continue
            assessment = f"<b>{escape(module.title)}</b>"
            if module.assessment_score is not None:
                assessment += f" · Score: {module.assessment_score}/100"
            if module.assessment_notes:
                assessment += f"<br/>{escape(module.assessment_notes).replace(chr(10), '<br/>')}"
            if module.benchmark_notes:
                assessment += f"<br/><b>Benchmark:</b> {escape(module.benchmark_notes).replace(chr(10), '<br/>')}"
            story.extend([Paragraph(assessment, styles["FieldnotesBody"]), Spacer(1, 6)])
    evidence_items = [item for module in modules for item in module.evidence.all()]
    if evidence_items:
        story.append(Paragraph("Evidence register", styles["FieldnotesSection"]))
        for item in evidence_items:
            evidence_text = f"<b>{escape(item.label)}</b> — {escape(item.module.title)}"
            if item.notes:
                evidence_text += f"<br/>{escape(item.notes).replace(chr(10), '<br/>')}"
            if item.source_url:
                evidence_text += f"<br/><font color='#71805d'>{escape(item.source_url)}</font>"
            if item.attachment:
                evidence_text += f"<br/><font color='#71805d'>File: {escape(item.attachment.name.rsplit('/', 1)[-1])}</font>"
            story.extend([Paragraph(evidence_text, styles["FieldnotesBody"]), Spacer(1, 7)])
    story.append(Paragraph("Findings and recommendations", styles["FieldnotesSection"]))
    if not findings:
        story.append(Paragraph("No findings were recorded for this audit.", styles["FieldnotesBody"]))
    for index, finding in enumerate(findings, start=1):
        story.extend([
            Paragraph(f"{index:02d}  {escape(finding.title)}", styles["Heading3"]),
            Paragraph(
                f"<b>{escape(finding.module.title)} · {escape(finding.get_priority_display())} priority</b>",
                styles["FieldnotesMeta"],
            ),
            Spacer(1, 4),
            *(
                [Paragraph(f"<b>Problem:</b> {escape(finding.problem).replace(chr(10), '<br/>')}", styles["FieldnotesBody"]), Spacer(1, 4)]
                if finding.problem else []
            ),
            *(
                [Paragraph(f"<b>Why it matters:</b> {escape(finding.why_it_matters).replace(chr(10), '<br/>')}", styles["FieldnotesBody"]), Spacer(1, 4)]
                if finding.why_it_matters else []
            ),
            Paragraph(f"<b>Evidence:</b> {escape(finding.evidence).replace(chr(10), '<br/>')}", styles["FieldnotesBody"]),
            Spacer(1, 4),
            Paragraph(f"<b>Impact:</b> {escape(finding.impact).replace(chr(10), '<br/>')}", styles["FieldnotesBody"]),
            Spacer(1, 4),
            Paragraph(f"<b>Recommended action:</b> {escape(finding.recommendation).replace(chr(10), '<br/>')}", styles["FieldnotesBody"]),
            *(
                [Paragraph(f"<b>Strategic opportunity:</b> {escape(finding.strategic_opportunity).replace(chr(10), '<br/>')}", styles["FieldnotesBody"])]
                if finding.strategic_opportunity else []
            ),
            Paragraph(
                f"Priority: {escape(finding.get_priority_display())}"
                + (f" · Owner: {escape(finding.owner_name)}" if finding.owner_name else "")
                + (f" · Target: {finding.due_date:%b %d, %Y}" if finding.due_date else ""),
                styles["FieldnotesMeta"],
            ),
            Spacer(1, 12),
        ])

    roadmap = audit.roadmap_data or {}
    kpis = roadmap.get("kpis", [])
    roadmap_items = roadmap.get("items", [])
    if kpis or roadmap_items:
        story.append(Paragraph("Implementation roadmap", styles["FieldnotesSection"]))
        for kpi in kpis:
            story.append(Paragraph(
                f"<b>{escape(str(kpi.get('name', 'KPI')))}</b>: "
                f"{escape(str(kpi.get('baseline', '—')))} → {escape(str(kpi.get('target', '—')))} "
                f"{escape(str(kpi.get('unit', '')))}",
                styles["FieldnotesBody"],
            ))
        for item in roadmap_items:
            story.append(Paragraph(
                f"<b>{escape(str(item.get('phase', '')))} · {escape(str(item.get('title', 'Roadmap action')))}</b>"
                + (f"<br/>{escape(str(item.get('description', '')))}" if item.get("description") else "")
                + (f"<br/><font color='#83867b'>Owner: {escape(str(item.get('owner', 'Unassigned')))}</font>" if item.get("owner") else ""),
                styles["FieldnotesBody"],
            ))
            story.append(Spacer(1, 5))

    def add_page_number(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor("#DDE4EE"))
        canvas.line(20 * mm, 15 * mm, A4[0] - 20 * mm, 15 * mm)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(20 * mm, 10 * mm, f"VE Studio · {audit.client}")
        canvas.drawRightString(A4[0] - 20 * mm, 10 * mm, f"Page {document.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=add_page_number, onLaterPages=add_page_number)
    buffer.seek(0)
    filename = f"{slugify(audit.client) or 'client'}-audit-report.pdf"
    return FileResponse(buffer, as_attachment=True, filename=filename, content_type="application/pdf")


@api_view(["GET"])
@permission_classes([permissions.AllowAny])
def health_check(request):
    return Response({"status": "ok", "time": timezone.now()})
