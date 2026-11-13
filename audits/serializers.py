import os

from django.contrib.auth import get_user_model, password_validation
from django.contrib.auth.models import update_last_login
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, URLValidator
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.tokens import RefreshToken

from .catalog import ENGAGEMENT_PACKAGES, MODULE_CATALOG
from .models import ALLOWED_EVIDENCE_EXTENSIONS, Audit, AuditModule, Evidence, Finding


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    username_field = "email"

    def validate(self, attrs):
        email = self.initial_data.get("email", "").strip()
        password = self.initial_data.get("password", "")
        if not email or not password:
            raise serializers.ValidationError({"detail": "Email and password are required."})
        from django.contrib.auth import authenticate

        user = authenticate(request=self.context.get("request"), username=email, password=password)
        if user is None:
            raise serializers.ValidationError({"detail": "Email or password is incorrect."})
        self.user = user
        refresh = self.get_token(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
            "name": user.get_full_name() or user.email or user.get_username(),
            "is_staff": user.is_staff,
        }


class RegisterSerializer(serializers.Serializer):
    first_name = serializers.CharField(max_length=150, trim_whitespace=True)
    last_name = serializers.CharField(max_length=150, trim_whitespace=True)
    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(write_only=True, trim_whitespace=False, min_length=8)
    password_confirm = serializers.CharField(write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        email = value.strip().lower()
        user_model = get_user_model()
        if user_model.objects.filter(email__iexact=email).exists():
            raise serializers.ValidationError("An account with this email already exists. Sign in instead.")
        if user_model.objects.filter(username__iexact=email).exists():
            raise serializers.ValidationError("This email cannot be used for a new account.")
        return email

    def validate(self, attrs):
        if attrs["password"] != attrs["password_confirm"]:
            raise serializers.ValidationError({"password_confirm": "Passwords do not match."})
        user = get_user_model()(
            username=attrs["email"],
            email=attrs["email"],
            first_name=attrs["first_name"],
            last_name=attrs["last_name"],
        )
        try:
            password_validation.validate_password(attrs["password"], user)
        except ValidationError as error:
            raise serializers.ValidationError({"password": list(error.messages)}) from error
        return attrs

    def create(self, validated_data):
        validated_data.pop("password_confirm")
        password = validated_data.pop("password")
        user = get_user_model().objects.create_user(
            username=validated_data["email"],
            password=password,
            **validated_data,
        )
        refresh = RefreshToken.for_user(user)
        update_last_login(None, user)
        return {
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "name": user.get_full_name(),
            "email": user.email,
            "is_staff": user.is_staff,
        }


class AuditModuleSerializer(serializers.ModelSerializer):
    evidence_count = serializers.IntegerField(source="evidence.count", read_only=True)

    class Meta:
        model = AuditModule
        fields = [
            "id", "key", "title", "category", "required", "status", "evidence_note",
            "assessment_score", "assessment_notes", "benchmark_notes", "evidence_count", "position",
        ]
        read_only_fields = ["id", "key", "title", "category", "required", "position"]

    def validate_assessment_score(self, value):
        if value is not None and not 0 <= value <= 100:
            raise serializers.ValidationError("Assessment scores must be between 0 and 100.")
        return value

    def validate(self, attrs):
        status = attrs.get("status", getattr(self.instance, "status", None))
        if status == AuditModule.Status.COMPLETE:
            evidence = self.instance.evidence if self.instance is not None else None
            if evidence is None or not evidence.exists():
                raise serializers.ValidationError({"status": "Add at least one evidence item before completing this module."})
        return attrs


class EvidenceSerializer(serializers.ModelSerializer):
    added_by = serializers.CharField(source="added_by.get_full_name", read_only=True)
    attachment = serializers.FileField(
        required=False,
        write_only=True,
        validators=[FileExtensionValidator(ALLOWED_EVIDENCE_EXTENSIONS)],
    )
    attachment_name = serializers.SerializerMethodField()
    attachment_size = serializers.SerializerMethodField()
    has_attachment = serializers.SerializerMethodField()

    class Meta:
        model = Evidence
        fields = [
            "id", "audit", "module", "label", "source_url", "notes", "added_by", "created_at",
            "attachment", "attachment_name", "attachment_size", "has_attachment",
        ]
        read_only_fields = ["id", "added_by", "created_at", "attachment_name", "attachment_size", "has_attachment"]

    def get_attachment_name(self, obj):
        return os.path.basename(obj.attachment.name) if obj.attachment else ""

    def get_attachment_size(self, obj):
        return obj.attachment.size if obj.attachment else 0

    def get_has_attachment(self, obj):
        return bool(obj.attachment)

    def validate_attachment(self, value):
        if value and value.size > 10 * 1024 * 1024:
            raise serializers.ValidationError("Evidence files must be 10 MB or smaller.")
        return value

    def validate(self, attrs):
        audit = attrs.get("audit", getattr(self.instance, "audit", None))
        module = attrs.get("module", getattr(self.instance, "module", None))
        request = self.context.get("request")
        if request and audit and not request.user.is_staff and audit.owner_id != request.user.id:
            raise serializers.ValidationError({"audit": "You do not have access to this audit."})
        if module and audit and module.audit_id != audit.id:
            raise serializers.ValidationError({"module": "Evidence and module must belong to the same audit."})
        label = attrs.get("label", getattr(self.instance, "label", ""))
        source_url = attrs.get("source_url", getattr(self.instance, "source_url", ""))
        notes = attrs.get("notes", getattr(self.instance, "notes", ""))
        attachment = attrs.get("attachment", getattr(self.instance, "attachment", None))
        if not label.strip():
            raise serializers.ValidationError({"label": "Add a name for this evidence item."})
        if not source_url and not notes.strip() and not attachment:
            raise serializers.ValidationError({"attachment": "Add a source link, context note, or file."})
        return attrs


class FindingSerializer(serializers.ModelSerializer):
    module_title = serializers.CharField(source="module.title", read_only=True)

    class Meta:
        model = Finding
        fields = [
            "id", "audit", "module", "module_title", "title", "problem", "why_it_matters", "evidence", "impact",
            "recommendation", "strategic_opportunity", "priority", "status", "owner_name", "due_date",
            "created_at",
        ]
        read_only_fields = ["id", "created_at"]

    def validate(self, attrs):
        audit = attrs.get("audit", getattr(self.instance, "audit", None))
        module = attrs.get("module", getattr(self.instance, "module", None))
        if module and audit and module.audit_id != audit.id:
            raise serializers.ValidationError({"module": "Finding and module must belong to the same audit."})
        required_fields = ("title", "problem", "why_it_matters", "evidence", "impact", "recommendation", "strategic_opportunity")
        missing = [
            field for field in required_fields
            if not str(attrs.get(field, getattr(self.instance, field, ""))).strip()
        ]
        if missing:
            raise serializers.ValidationError({field: "Complete this part of the structured finding." for field in missing})
        return attrs


class AuditSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source="audit_type", required=False)
    status = serializers.SerializerMethodField()
    owner = serializers.SerializerMethodField()
    date = serializers.DateTimeField(source="created_at", format="%b %d, %Y", read_only=True)
    progress = serializers.SerializerMethodField()
    modules = serializers.SerializerMethodField()
    initials = serializers.SerializerMethodField()
    color = serializers.SerializerMethodField()
    module_keys = serializers.SerializerMethodField()
    shared = serializers.SerializerMethodField()
    shared_at = serializers.DateTimeField(format="%b %d, %Y, %I:%M %p", read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    selected_modules = serializers.ListField(
        child=serializers.CharField(max_length=40), write_only=True, required=False,
    )
    team_assignments = serializers.JSONField(required=False)
    client_assets = serializers.JSONField(required=False)
    roadmap_data = serializers.JSONField(required=False)
    quality_checks = serializers.JSONField(required=False, read_only=True)
    delivery_data = serializers.JSONField(required=False)
    commercial_data = serializers.JSONField(required=False)

    class Meta:
        model = Audit
        fields = [
            "id", "client", "type", "goal", "contact_email", "website_url", "industry",
            "audit_category", "team_assignments", "client_assets", "roadmap_data",
            "quality_checks", "delivery_data", "commercial_data", "status", "owner", "date",
            "approved_at", "approved_by_name",
            "progress", "modules", "initials", "color", "selected_modules", "module_keys",
            "review_feedback", "shared", "shared_at",
        ]
        read_only_fields = [
            "id", "status", "owner", "date", "progress", "modules", "initials", "color",
            "module_keys", "review_feedback", "shared", "shared_at",
            "approved_at", "approved_by_name",
        ]

    def get_status(self, obj):
        return obj.get_status_display()

    def get_owner(self, obj):
        return obj.owner.get_full_name() or obj.owner.email or obj.owner.get_username()

    def get_progress(self, obj):
        total = obj.modules.count()
        if not total:
            return 0
        completed = obj.modules.filter(status=AuditModule.Status.COMPLETE).count()
        return round(completed / total * 100)

    def get_modules(self, obj):
        total = obj.modules.count()
        completed = obj.modules.filter(status=AuditModule.Status.COMPLETE).count()
        return f"{completed} of {total} modules"

    def get_initials(self, obj):
        return "".join(part[0] for part in obj.client.split()[:2]).upper()

    def get_color(self, obj):
        return ["peach", "mint", "lavender", "blue"][obj.pk % 4]

    def get_module_keys(self, obj):
        return list(obj.modules.values_list("key", flat=True))

    def get_shared(self, obj):
        return obj.share_token is not None

    def get_approved_by_name(self, obj):
        if obj.approved_by is None:
            return ""
        return obj.approved_by.get_full_name() or obj.approved_by.email or obj.approved_by.get_username()

    def validate_client(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Enter the client organization name.")
        return value

    def validate_website_url(self, value):
        value = value.strip()
        if value:
            try:
                URLValidator(schemes=["http", "https"])(value)
            except ValidationError as error:
                raise serializers.ValidationError("Enter a valid HTTP or HTTPS website URL.") from error
        return value

    def validate_selected_modules(self, value):
        if not value:
            raise serializers.ValidationError("Select at least one audit module.")
        unknown = set(value) - MODULE_CATALOG.keys()
        if unknown:
            raise serializers.ValidationError(f"Unknown module key(s): {', '.join(sorted(unknown))}.")
        for key, (_, required, _) in MODULE_CATALOG.items():
            if required and key not in value:
                raise serializers.ValidationError("All required audit modules must be included.")
        return list(dict.fromkeys(value))

    def validate_team_assignments(self, value):
        if not isinstance(value, dict) or not isinstance(value.get("smes", []), list):
            raise serializers.ValidationError("Team assignments must include an SME list.")
        for specialist in value.get("smes", []):
            if not isinstance(specialist, dict) or not str(specialist.get("name", "")).strip():
                raise serializers.ValidationError("Each subject matter expert needs a name.")
        return value

    def validate_client_assets(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("Client assets must be a list.")
        for asset in value:
            if not isinstance(asset, dict) or not str(asset.get("label", "")).strip():
                raise serializers.ValidationError("Each client asset needs a name.")
            if asset.get("url"):
                try:
                    URLValidator(schemes=["http", "https"])(str(asset["url"]).strip())
                except ValidationError as error:
                    raise serializers.ValidationError("Client asset URLs must use HTTP or HTTPS.") from error
        return value

    def validate_roadmap_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Roadmap data must be an object.")
        for key in ("items", "kpis"):
            if key in value and not isinstance(value[key], list):
                raise serializers.ValidationError(f"Roadmap {key} must be a list.")
        for item in value.get("items", []):
            if not isinstance(item, dict) or not str(item.get("title", "")).strip():
                raise serializers.ValidationError("Each roadmap action needs a title.")
        for kpi in value.get("kpis", []):
            if not isinstance(kpi, dict) or not str(kpi.get("name", "")).strip():
                raise serializers.ValidationError("Each roadmap KPI needs a name.")
        return value

    def validate_delivery_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Delivery details must be an object.")
        return value

    def validate_commercial_data(self, value):
        if not isinstance(value, dict):
            raise serializers.ValidationError("Commercial details must be an object.")
        if value.get("status") == "won":
            raise serializers.ValidationError("Use the close-deal workflow action to record a won engagement.")
        package_id = value.get("package_id", "")
        if package_id:
            package = ENGAGEMENT_PACKAGES.get(package_id)
            if package is None:
                raise serializers.ValidationError("Select one of the available implementation packages.")
            value = {**value, **package}
        if len(str(value.get("notes", ""))) > 4000:
            raise serializers.ValidationError({"notes": "Proposal notes must be 4,000 characters or fewer."})
        return value

    def create(self, validated_data):
        selected_modules = validated_data.pop("selected_modules", None)
        for field in ("contact_email", "website_url", "industry", "audit_category"):
            validated_data.setdefault(field, "")
        audit = Audit.objects.create(owner=self.context["request"].user, **validated_data)
        keys = selected_modules or [key for key, (_, required, _) in MODULE_CATALOG.items() if required]
        AuditModule.objects.bulk_create([
            AuditModule(
                audit=audit,
                key=key,
                title=MODULE_CATALOG[key][0],
                required=MODULE_CATALOG[key][1],
                category=MODULE_CATALOG[key][2],
                position=index,
            )
            for index, key in enumerate(keys)
        ])
        return audit


class SharedFindingSerializer(serializers.ModelSerializer):
    module_title = serializers.CharField(source="module.title", read_only=True)

    class Meta:
        model = Finding
        fields = [
            "module_title", "title", "problem", "why_it_matters", "evidence", "impact", "recommendation",
            "strategic_opportunity", "priority", "owner_name", "due_date",
        ]


class SharedAuditSerializer(serializers.ModelSerializer):
    type = serializers.CharField(source="audit_type", read_only=True)
    findings = SharedFindingSerializer(many=True, read_only=True)

    class Meta:
        model = Audit
        fields = [
            "client", "type", "goal", "website_url", "industry", "audit_category",
            "roadmap_data", "findings",
        ]
