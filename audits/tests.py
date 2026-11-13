import os
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from rest_framework.test import APITestCase

from .models import Audit, AuditModule, Evidence, Finding


class AuditWorkflowTests(APITestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user(
            username="ariana",
            email="ariana@example.com",
            password="Secure-Test-Password-27!",
            first_name="Ariana",
            last_name="James",
        )
        self.other_user = get_user_model().objects.create_user(
            username="marcus",
            email="marcus@example.com",
            password="Secure-Test-Password-83!",
        )

    def sign_in(self):
        response = self.client.post(
            "/api/auth/token/",
            {"email": "ARIANA@example.com", "password": "Secure-Test-Password-27!"},
            format="json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertNotIn("refresh", response.data)
        cookie = response.cookies["fieldnotes_refresh"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        return response.data["access"]

    def test_email_login_and_owner_scoped_audit_list(self):
        Audit.objects.create(client="Private client", owner=self.other_user)
        login = self.client.post(
            "/api/auth/token/",
            {"email": "ARIANA@example.com", "password": "Secure-Test-Password-27!"},
            format="json",
        )
        self.assertEqual(login.status_code, 200)
        self.assertEqual(login.data["name"], "Ariana James")
        self.assertFalse(login.data["is_staff"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        response = self.client.get("/api/audits/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 0)

    def test_registration_creates_account_and_returns_login_tokens(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "first_name": "Taylor",
                "last_name": "Morgan",
                "email": "Taylor@Example.com",
                "password": "Strong-Registration-Password-27!",
                "password_confirm": "Strong-Registration-Password-27!",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["email"], "taylor@example.com")
        self.assertTrue(response.data["access"])
        self.assertNotIn("refresh", response.data)
        self.assertTrue(response.cookies["fieldnotes_refresh"]["httponly"])
        account = get_user_model().objects.get(email="taylor@example.com")
        self.assertEqual(account.get_full_name(), "Taylor Morgan")
        self.assertFalse(account.is_staff)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        self.assertEqual(self.client.get("/api/audits/").data["count"], 0)

    def test_refresh_rotates_http_only_cookie_and_rejects_reuse(self):
        self.sign_in()
        old_refresh = self.client.cookies["fieldnotes_refresh"].value
        response = self.client.post("/api/auth/token/refresh/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["access"])
        self.assertNotIn("refresh", response.data)
        rotated_refresh = response.cookies["fieldnotes_refresh"].value
        self.assertNotEqual(old_refresh, rotated_refresh)

        self.client.cookies["fieldnotes_refresh"] = old_refresh
        replay = self.client.post("/api/auth/token/refresh/", {}, format="json")
        self.assertEqual(replay.status_code, 401)

    def test_logout_blacklists_refresh_cookie(self):
        self.sign_in()
        logout = self.client.post("/api/auth/logout/", {}, format="json")
        self.assertEqual(logout.status_code, 204)
        refresh = self.client.post("/api/auth/token/refresh/", {}, format="json")
        self.assertEqual(refresh.status_code, 401)

    def test_registration_rejects_duplicate_email_and_mismatched_passwords(self):
        payload = {
            "first_name": "Ariana",
            "last_name": "James",
            "email": "ARiAna@example.com",
            "password": "Strong-Registration-Password-27!",
            "password_confirm": "Strong-Registration-Password-27!",
        }
        duplicate = self.client.post("/api/auth/register/", payload, format="json")
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn("email", duplicate.data)

        payload["email"] = "new@example.com"
        payload["password_confirm"] = "Different-Password-27!"
        mismatch = self.client.post("/api/auth/register/", payload, format="json")
        self.assertEqual(mismatch.status_code, 400)
        self.assertIn("password_confirm", mismatch.data)

    def test_registration_enforces_django_password_validators(self):
        response = self.client.post(
            "/api/auth/register/",
            {
                "first_name": "Taylor",
                "last_name": "Morgan",
                "email": "taylor@example.com",
                "password": "password",
                "password_confirm": "password",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)

    def test_create_audit_includes_required_modules(self):
        self.sign_in()
        response = self.client.post(
            "/api/audits/",
            {
                "client": "Northstar Living",
                "type": "Digital growth audit",
                "goal": "Improve qualified demand.",
                "selected_modules": ["business", "website", "seo", "measurement"],
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        audit = Audit.objects.get(pk=response.data["id"])
        self.assertEqual(audit.modules.count(), 4)
        self.assertEqual(audit.audit_category, "")
        self.assertEqual(response.data["module_keys"], ["business", "website", "seo", "measurement"])

    def test_cannot_complete_module_without_evidence_or_skip_required_checks(self):
        self.sign_in()
        audit = Audit.objects.create(client="Northstar Living", owner=self.owner)
        module = AuditModule.objects.create(
            audit=audit, key="business", title="Business & goals", required=True, category="FOUNDATION",
        )
        AuditModule.objects.create(
            audit=audit, key="website", title="Website & experience", required=True, category="OWNED CHANNELS",
        )
        AuditModule.objects.create(
            audit=audit, key="seo", title="SEO & discoverability", required=True, category="OWNED CHANNELS",
        )
        AuditModule.objects.create(
            audit=audit, key="measurement", title="Measurement & data", required=True, category="FOUNDATION",
        )
        update = self.client.patch(f"/api/modules/{module.id}/", {"status": "complete"}, format="json")
        self.assertEqual(update.status_code, 400)

        Evidence.objects.create(
            audit=audit, module=module, label="Client brief", notes="Goals confirmed.", added_by=self.owner,
        )
        update = self.client.patch(f"/api/modules/{module.id}/", {"status": "complete"}, format="json")
        self.assertEqual(update.status_code, 200)
        modules = self.client.get(f"/api/modules/?audit={audit.id}")
        business_module = next(record for record in modules.data["results"] if record["key"] == "business")
        self.assertEqual(business_module["evidence_count"], 1)
        submit = self.client.post(f"/api/audits/{audit.id}/submit_for_review/", {}, format="json")
        self.assertEqual(submit.status_code, 400)
        self.assertIn("missing_modules", submit.data)

    def test_submitted_audit_appears_in_quality_review_queue(self):
        self.sign_in()
        audit = Audit.objects.create(client="Review-ready client", owner=self.owner)
        for key, title, category in [
            ("business", "Business & goals", "FOUNDATION"),
            ("website", "Website & experience", "OWNED CHANNELS"),
            ("seo", "SEO & discoverability", "OWNED CHANNELS"),
            ("measurement", "Measurement & data", "FOUNDATION"),
        ]:
            module = AuditModule.objects.create(
                audit=audit, key=key, title=title, required=True, category=category,
                status=AuditModule.Status.COMPLETE,
            )
            Evidence.objects.create(
                audit=audit, module=module, label=f"{title} evidence",
                notes="Reviewed and attached.", added_by=self.owner,
            )

        submitted = self.client.post(f"/api/audits/{audit.id}/submit_for_review/", {}, format="json")
        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(submitted.data["status"], "In review")
        queue = self.client.get("/api/audits/review_queue/")
        self.assertEqual(queue.status_code, 200)
        self.assertEqual([item["id"] for item in queue.data], [audit.id])

    def test_review_queue_only_contains_submitted_audits_visible_to_user(self):
        submitted = Audit.objects.create(
            client="Submitted client", owner=self.owner, status=Audit.Status.IN_REVIEW,
        )
        Audit.objects.create(client="Draft client", owner=self.owner)
        Audit.objects.create(
            client="Another owner's client", owner=self.other_user, status=Audit.Status.IN_REVIEW,
        )
        self.sign_in()
        queue = self.client.get("/api/audits/review_queue/")
        self.assertEqual(queue.status_code, 200)
        self.assertEqual([audit["id"] for audit in queue.data], [submitted.id])

        self.client.force_authenticate(user=get_user_model().objects.create_superuser(
            username="reviewer", email="reviewer@example.com", password="Strong-Reviewer-Password-27!",
        ))
        reviewer_queue = self.client.get("/api/audits/review_queue/")
        self.assertEqual(reviewer_queue.status_code, 200)
        self.assertEqual(len(reviewer_queue.data), 2)

    def test_staff_can_return_audit_for_changes_and_owner_can_resubmit(self):
        audit = Audit.objects.create(
            client="Needs edits", owner=self.owner, status=Audit.Status.IN_REVIEW,
        )
        self.sign_in()
        forbidden = self.client.post(
            f"/api/audits/{audit.id}/request_changes/",
            {"feedback": "Add stronger conversion evidence."},
            format="json",
        )
        self.assertEqual(forbidden.status_code, 403)

        reviewer = get_user_model().objects.create_superuser(
            username="reviewer", email="reviewer@example.com", password="Reviewer-Test-Password-27!",
        )
        self.client.force_authenticate(user=reviewer)
        missing_feedback = self.client.post(
            f"/api/audits/{audit.id}/request_changes/", {"feedback": " "}, format="json",
        )
        self.assertEqual(missing_feedback.status_code, 400)
        returned = self.client.post(
            f"/api/audits/{audit.id}/request_changes/",
            {"feedback": "Add conversion evidence and clarify the recommendation owner."},
            format="json",
        )
        self.assertEqual(returned.status_code, 200)
        self.assertEqual(returned.data["status"], "Needs changes")
        self.assertEqual(
            returned.data["review_feedback"],
            "Add conversion evidence and clarify the recommendation owner.",
        )

        self.client.force_authenticate(user=self.owner)
        resubmitted = self.client.post(f"/api/audits/{audit.id}/submit_for_review/", {}, format="json")
        self.assertEqual(resubmitted.status_code, 200)
        self.assertEqual(resubmitted.data["status"], "In review")
        self.assertEqual(resubmitted.data["review_feedback"], "")

    def test_only_staff_can_approve_and_approved_audits_can_be_shared_and_revoked(self):
        audit = Audit.objects.create(
            client="Approved client", owner=self.owner, status=Audit.Status.IN_REVIEW,
        )
        module = AuditModule.objects.create(
            audit=audit, key="seo", title="SEO & discoverability", category="OWNED CHANNELS",
        )
        Finding.objects.create(
            audit=audit,
            module=module,
            title="Improve organic landing page conversion",
            evidence="Search traffic exits before contacting the business.",
            impact="Fewer qualified enquiries are generated.",
            recommendation="Test stronger proof and a clearer enquiry path.",
            priority=Finding.Priority.HIGH,
        )
        self.sign_in()
        denied = self.client.post(f"/api/audits/{audit.id}/approve/", {}, format="json")
        self.assertEqual(denied.status_code, 403)
        not_approved = self.client.post(f"/api/audits/{audit.id}/share/", {}, format="json")
        self.assertEqual(not_approved.status_code, 400)

        reviewer = get_user_model().objects.create_superuser(
            username="reviewer", email="reviewer@example.com", password="Reviewer-Test-Password-27!",
        )
        self.client.force_authenticate(user=reviewer)
        missing_quality_checks = self.client.post(f"/api/audits/{audit.id}/approve/", {}, format="json")
        self.assertEqual(missing_quality_checks.status_code, 400)
        checks = {
            "completeness": True,
            "technical_accuracy": True,
            "client_relevance": True,
            "clarity": True,
            "design": True,
            "methodology": True,
        }
        saved_checks = self.client.post(
            f"/api/audits/{audit.id}/quality_checks/",
            {"checks": checks, "notes": "Reviewed source material."},
            format="json",
        )
        self.assertEqual(saved_checks.status_code, 200)
        self.assertEqual(saved_checks.data["quality_checks"]["reviewer_notes"], "Reviewed source material.")
        self.assertEqual(saved_checks.data["quality_checks"]["checked_by"], "reviewer@example.com")
        approved = self.client.post(f"/api/audits/{audit.id}/approve/", {}, format="json")
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.data["status"], "Approved")
        self.assertEqual(approved.data["approved_by_name"], "reviewer@example.com")
        self.assertIsNotNone(approved.data["approved_at"])

        self.client.force_authenticate(user=self.owner)
        shared = self.client.post(f"/api/audits/{audit.id}/share/", {}, format="json")
        self.assertEqual(shared.status_code, 200)
        token = shared.data["token"]
        public_view = self.client.get(f"/api/shared-audits/{token}/")
        self.assertEqual(public_view.status_code, 200)
        self.assertEqual(public_view.data["client"], "Approved client")
        self.assertEqual(len(public_view.data["findings"]), 1)
        self.assertNotIn("evidence_count", public_view.data)

        revoked = self.client.post(f"/api/audits/{audit.id}/revoke_share/", {}, format="json")
        self.assertEqual(revoked.status_code, 204)
        self.assertEqual(self.client.get(f"/api/shared-audits/{token}/").status_code, 404)

    def test_audit_content_is_locked_while_in_review_and_after_approval(self):
        audit = Audit.objects.create(
            client="Locked client", owner=self.owner, status=Audit.Status.IN_REVIEW,
        )
        module = AuditModule.objects.create(
            audit=audit, key="seo", title="SEO & discoverability", category="OWNED CHANNELS",
        )
        self.sign_in()
        audit_update = self.client.patch(
            f"/api/audits/{audit.id}/", {"goal": "Change while under review."}, format="json",
        )
        self.assertEqual(audit_update.status_code, 400)
        module_update = self.client.patch(
            f"/api/modules/{module.id}/", {"evidence_note": "Change while under review."}, format="json",
        )
        self.assertEqual(module_update.status_code, 400)
        evidence_create = self.client.post(
            "/api/evidence/",
            {"audit": audit.id, "module": module.id, "label": "New evidence", "notes": "Not allowed yet."},
            format="json",
        )
        self.assertEqual(evidence_create.status_code, 400)

        audit.status = Audit.Status.APPROVED
        audit.save(update_fields=["status"])
        finding_create = self.client.post(
            "/api/findings/",
            {
                "audit": audit.id,
                "module": module.id,
                "title": "New finding",
                "evidence": "Evidence",
                "impact": "Impact",
                "recommendation": "Recommendation",
            },
            format="json",
        )
        self.assertEqual(finding_create.status_code, 400)

    def test_findings_are_scoped_and_include_client_ready_fields(self):
        self.sign_in()
        audit = Audit.objects.create(client="Northstar Living", owner=self.owner)
        module = AuditModule.objects.create(
            audit=audit, key="seo", title="SEO & discoverability", required=True, category="OWNED CHANNELS",
        )
        response = self.client.post(
            "/api/findings/",
            {
                "audit": audit.id,
                "module": module.id,
                "title": "Organic landing pages have weak conversion",
                "problem": "High-intent organic visitors leave without enquiring.",
                "why_it_matters": "The client is not capturing existing demand.",
                "evidence": "Search Console and analytics data show high-intent traffic leaving key pages.",
                "impact": "Qualified organic visits are not converting into enquiries.",
                "recommendation": "Test clearer proof points and a more direct enquiry path.",
                "strategic_opportunity": "Build an organic channel that supports efficient acquisition.",
                "priority": "critical",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["priority"], "critical")
        self.assertEqual(response.data["module_title"], "SEO & discoverability")
        self.assertEqual(self.client.get(f"/api/findings/?audit={audit.id}").data["count"], 1)

    def test_workflow_data_assessments_and_deal_follow_up_are_persisted(self):
        self.sign_in()
        audit = Audit.objects.create(client="Northstar Living", owner=self.owner)
        module = AuditModule.objects.create(
            audit=audit, key="seo", title="SEO & discoverability", required=True, category="OWNED CHANNELS",
        )
        profile = self.client.patch(
            f"/api/audits/{audit.id}/",
            {
                "contact_email": "marketing@example.com",
                "website_url": "https://example.com",
                "industry": "Retail",
                "audit_category": "Full digital growth audit",
                "team_assignments": {"lead_name": "A. Auditor", "smes": [{"name": "B. Specialist", "expertise": "SEO"}]},
                "client_assets": [{"id": "site", "category": "Website & sitemap", "label": "Main site", "url": "https://example.com", "status": "verified"}],
                "roadmap_data": {"kpis": [{"name": "Conversion rate", "baseline": "2%", "target": "3%", "unit": "%"}], "items": [{"title": "Improve landing page", "phase": "Days 1–30"}]},
                "delivery_data": {"presenter": "A. Auditor", "agenda": "Review priorities."},
                "commercial_data": {"package_id": "foundation-sprint", "package_name": "Foundation Fix Sprint", "price": 4500, "cadence": "14-day pilot", "status": "proposal_selected"},
            },
            format="json",
        )
        self.assertEqual(profile.status_code, 200, profile.data)
        self.assertEqual(profile.data["client_assets"][0]["status"], "verified")
        self.assertEqual(profile.data["roadmap_data"]["kpis"][0]["target"], "3%")

        updated_module = self.client.patch(
            f"/api/modules/{module.id}/",
            {"assessment_score": 72, "assessment_notes": "Technical review complete.", "benchmark_notes": "Compared with named competitors."},
            format="json",
        )
        self.assertEqual(updated_module.status_code, 200, updated_module.data)
        self.assertEqual(updated_module.data["assessment_score"], 72)

        incomplete_finding = self.client.post(
            "/api/findings/",
            {"audit": audit.id, "module": module.id, "title": "Incomplete finding", "evidence": "Source checked.", "impact": "Impact"},
            format="json",
        )
        self.assertEqual(incomplete_finding.status_code, 400)

        audit.status = Audit.Status.APPROVED
        audit.save(update_fields=["status"])
        delivery = self.client.patch(
            f"/api/audits/{audit.id}/",
            {"delivery_data": {"presenter": "A. Auditor", "agenda": "Review final priorities."}},
            format="json",
        )
        self.assertEqual(delivery.status_code, 200, delivery.data)
        forged_won_status = self.client.patch(
            f"/api/audits/{audit.id}/",
            {"commercial_data": {"package_id": "foundation-sprint", "status": "won"}},
            format="json",
        )
        self.assertEqual(forged_won_status.status_code, 400)
        won = self.client.post(f"/api/audits/{audit.id}/close_deal/", {}, format="json")
        self.assertEqual(won.status_code, 200)
        self.assertEqual(won.data["commercial_data"]["status"], "won")

    def test_users_cannot_read_or_change_another_owners_audit(self):
        audit = Audit.objects.create(client="Private client", owner=self.other_user)
        self.sign_in()
        self.assertEqual(self.client.get(f"/api/audits/{audit.id}/").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/audits/{audit.id}/").status_code, 404)

    def test_owner_can_delete_editable_audit_and_its_uploaded_evidence(self):
        audit = Audit.objects.create(client="Delete me", owner=self.owner)
        module = AuditModule.objects.create(
            audit=audit, key="business", title="Business & goals", required=True, category="FOUNDATION",
        )
        self.sign_in()
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            upload = SimpleUploadedFile("brief.txt", b"Private client data.", content_type="text/plain")
            evidence = self.client.post(
                "/api/evidence/",
                {"audit": audit.id, "module": module.id, "label": "Client brief", "attachment": upload},
                format="multipart",
            )
            self.assertEqual(evidence.status_code, 201)
            attachment_path = Evidence.objects.get(pk=evidence.data["id"]).attachment.path
            self.assertTrue(os.path.exists(attachment_path))
            with self.captureOnCommitCallbacks(execute=True):
                deleted = self.client.delete(f"/api/audits/{audit.id}/")
            self.assertEqual(deleted.status_code, 204)
            self.assertFalse(Audit.objects.filter(pk=audit.id).exists())
            self.assertFalse(Evidence.objects.filter(pk=evidence.data["id"]).exists())
            self.assertFalse(os.path.exists(attachment_path))

    def test_audit_delete_is_blocked_during_review_and_after_approval(self):
        self.sign_in()
        for audit_status in (Audit.Status.IN_REVIEW, Audit.Status.APPROVED):
            audit = Audit.objects.create(client=f"Protected {audit_status}", owner=self.owner, status=audit_status)
            response = self.client.delete(f"/api/audits/{audit.id}/")
            self.assertEqual(response.status_code, 400)
            self.assertTrue(Audit.objects.filter(pk=audit.id).exists())

    def test_evidence_file_upload_is_private_and_downloadable_by_owner(self):
        audit = Audit.objects.create(client="File evidence client", owner=self.owner)
        module = AuditModule.objects.create(
            audit=audit, key="business", title="Business & goals", required=True, category="FOUNDATION",
        )
        self.sign_in()
        with TemporaryDirectory() as media_root, override_settings(MEDIA_ROOT=media_root):
            upload = SimpleUploadedFile("client-brief.txt", b"Business goals and audience.", content_type="text/plain")
            response = self.client.post(
                "/api/evidence/",
                {"audit": audit.id, "module": module.id, "label": "Client brief", "attachment": upload},
                format="multipart",
            )
            self.assertEqual(response.status_code, 201, response.data)
            self.assertTrue(response.data["has_attachment"])
            self.assertEqual(response.data["attachment_name"], "client-brief.txt")
            self.assertGreater(response.data["attachment_size"], 0)
            self.assertNotIn("attachment", response.data)

            download = self.client.get(f"/api/evidence/{response.data['id']}/download/")
            self.assertEqual(download.status_code, 200)
            self.assertEqual(b"".join(download.streaming_content), b"Business goals and audience.")
            self.assertIn("attachment", download["Content-Disposition"])

            other_client = self.client
            other_client.force_authenticate(user=self.other_user)
            forbidden = other_client.get(f"/api/evidence/{response.data['id']}/download/")
            self.assertEqual(forbidden.status_code, 404)

    def test_evidence_file_rejects_unsupported_extensions_and_oversized_files(self):
        audit = Audit.objects.create(client="File validation client", owner=self.owner)
        module = AuditModule.objects.create(
            audit=audit, key="business", title="Business & goals", required=True, category="FOUNDATION",
        )
        self.sign_in()
        unsupported = SimpleUploadedFile("payload.exe", b"not allowed")
        response = self.client.post(
            "/api/evidence/",
            {"audit": audit.id, "module": module.id, "label": "Unsupported", "attachment": unsupported},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

        oversized = SimpleUploadedFile("oversized.txt", b"x" * (10 * 1024 * 1024 + 1))
        response = self.client.post(
            "/api/evidence/",
            {"audit": audit.id, "module": module.id, "label": "Too large", "attachment": oversized},
            format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_only_approved_audits_can_download_pdf_report(self):
        audit = Audit.objects.create(client="Quarterly report client", owner=self.owner)
        module = AuditModule.objects.create(
            audit=audit, key="seo", title="SEO & discoverability", category="OWNED CHANNELS",
            status=AuditModule.Status.COMPLETE,
        )
        Finding.objects.create(
            audit=audit,
            module=module,
            title="Improve organic conversion",
            evidence="High-intent visits are not converting.",
            impact="Qualified leads are lost.",
            recommendation="Test a clearer enquiry journey.",
            priority=Finding.Priority.HIGH,
        )
        self.sign_in()
        blocked = self.client.get(f"/api/audits/{audit.id}/report/")
        self.assertEqual(blocked.status_code, 400)

        audit.status = Audit.Status.APPROVED
        audit.save(update_fields=["status"])
        report = self.client.get(f"/api/audits/{audit.id}/report/")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report["Content-Type"], "application/pdf")
        self.assertTrue(b"".join(report.streaming_content).startswith(b"%PDF-"))
