from django.conf import settings
from django.db import models
from django.core.validators import FileExtensionValidator


ALLOWED_EVIDENCE_EXTENSIONS = ["pdf", "png", "jpg", "jpeg", "csv", "xlsx", "docx", "txt"]


class Audit(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        IN_PROGRESS = "in_progress", "In progress"
        IN_REVIEW = "in_review", "In review"
        NEEDS_CHANGES = "needs_changes", "Needs changes"
        APPROVED = "approved", "Approved"

    client = models.CharField(max_length=180)
    audit_type = models.CharField(max_length=80, default="Digital growth audit")
    goal = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True, default="")
    website_url = models.URLField(max_length=1000, blank=True, default="")
    industry = models.CharField(max_length=120, blank=True, default="")
    audit_category = models.CharField(max_length=80, blank=True, default="")
    team_assignments = models.JSONField(default=dict, blank=True)
    client_assets = models.JSONField(default=list, blank=True)
    roadmap_data = models.JSONField(default=dict, blank=True)
    quality_checks = models.JSONField(default=dict, blank=True)
    delivery_data = models.JSONField(default=dict, blank=True)
    commercial_data = models.JSONField(default=dict, blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="audits")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    review_feedback = models.TextField(blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="approved_audits",
    )
    share_token = models.UUIDField(null=True, blank=True, unique=True, editable=False)
    shared_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.client} — {self.audit_type}"


class AuditModule(models.Model):
    class Status(models.TextChoices):
        NOT_STARTED = "not_started", "Not started"
        IN_PROGRESS = "in_progress", "In progress"
        COMPLETE = "complete", "Complete"

    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name="modules")
    key = models.CharField(max_length=40)
    title = models.CharField(max_length=120)
    category = models.CharField(max_length=50)
    required = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.NOT_STARTED)
    evidence_note = models.TextField(blank=True)
    assessment_score = models.PositiveSmallIntegerField(null=True, blank=True)
    assessment_notes = models.TextField(blank=True)
    benchmark_notes = models.TextField(blank=True)
    position = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["position", "id"]
        constraints = [
            models.UniqueConstraint(fields=["audit", "key"], name="unique_audit_module_key"),
        ]

    def __str__(self):
        return f"{self.audit.client}: {self.title}"


class Evidence(models.Model):
    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name="evidence")
    module = models.ForeignKey(AuditModule, on_delete=models.CASCADE, related_name="evidence")
    label = models.CharField(max_length=180)
    source_url = models.URLField(max_length=1000, blank=True)
    notes = models.TextField(blank=True)
    attachment = models.FileField(
        upload_to="audit_evidence/%Y/%m/",
        blank=True,
        validators=[FileExtensionValidator(ALLOWED_EVIDENCE_EXTENSIONS)],
    )
    added_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="audit_evidence")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]


class Finding(models.Model):
    class Priority(models.TextChoices):
        CRITICAL = "critical", "Critical"
        HIGH = "high", "High"
        MEDIUM = "medium", "Medium"
        LOW = "low", "Low"

    class Status(models.TextChoices):
        OPEN = "open", "Open"
        PLANNED = "planned", "Planned"
        DONE = "done", "Done"

    audit = models.ForeignKey(Audit, on_delete=models.CASCADE, related_name="findings")
    module = models.ForeignKey(AuditModule, on_delete=models.PROTECT, related_name="findings")
    title = models.CharField(max_length=220)
    problem = models.TextField(blank=True)
    why_it_matters = models.TextField(blank=True)
    evidence = models.TextField()
    impact = models.TextField()
    recommendation = models.TextField()
    strategic_opportunity = models.TextField(blank=True)
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.OPEN)
    owner_name = models.CharField(max_length=120, blank=True)
    due_date = models.DateField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.title
