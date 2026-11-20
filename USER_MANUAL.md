# AuditPulse user manual

AuditPulse is a digital marketing audit workspace for collecting evidence, assessing client performance, recording recommendations, completing an independent quality review, and delivering an approved report.

## Features

- Email-based sign-in and account registration
- A dashboard for active, review-ready, approved, and recently updated audits
- A ten-stage client audit workflow
- Required and optional audit modules
- Evidence, uploads, source links, assessment scores, and benchmark notes
- Structured findings with priority, impact, recommendation, owner, and due date
- Roadmap, KPI, client-delivery, and commercial follow-up tracking
- Independent quality review, change requests, and approval controls
- Branded PDF report downloads for approved audits
- Secure, revocable client share links for approved audits

## Roles

### Audit owner

An audit owner creates and completes audits. Owners can edit an audit while it is **In progress** or **Needs changes**, submit it for review, download the approved PDF, and manage a client link after approval.

The current `admin@gmail.com` account is an audit owner. It is **not** a staff or reviewer account, so it cannot approve audits or create a superuser.

### Reviewer or superadmin

A reviewer must be a Django staff user. Reviewers can see the quality-review queue, request changes, complete quality checks, and approve eligible audits.

Create the requested reviewer on the deployment host (not through the public registration form):

```bash
python manage.py createsuperuser --username superadmin@gmail.com --email superadmin@gmail.com
```

When prompted, set the password provided through your secure credential channel, sign in once, and change it immediately. Never add account passwords to this repository or this document.

## Sign in

1. Open the AuditPulse frontend.
2. Enter your work email and password.
3. Select **Sign in**.
4. The overview dashboard opens after successful authentication.

If a browser displays **Service unavailable**, verify that the frontend was rebuilt with `VITE_API_URL=https://auditpulseapi.vestaging.in` and that the backend allows `https://auditpulse.vestaging.in` in `CORS_ALLOWED_ORIGINS`.

## Create an audit

1. From **Overview**, select **Create an audit**.
2. Enter the client name, audit type, goal, contact details, website, industry, and category.
3. Keep the required modules selected: **Business & goals**, **Website & experience**, **SEO & discoverability**, and **Measurement & data**.
4. Add optional modules when they are relevant, such as Paid media, Content & brand, Social & community, or Market & competitors.
5. Select **Create** to open the audit workspace.

## Worked example: Northwest Living audit

Use this fictional data to practise the complete workflow. It is mock data only - do not use it as a real client record.

### 1. Create the audit

| Field | Example value |
| --- | --- |
| Client name | Northwest Living |
| Audit type | Digital growth audit |
| Goal | Increase qualified enquiry leads by 25% in six months |
| Primary contact | Maya Patel, Marketing Director, maya@example.test |
| Website | https://northwest-living.example.test |
| Industry | Property and interiors |
| Category | Lead generation |
| Required modules | Business & goals, Website & experience, SEO & discoverability, Measurement & data |
| Optional modules | Paid media, Content & brand |

Select **Create**. The new audit appears on **Overview** with a status of **In progress**.

### 2. Add evidence and assessment data

Complete each required module with at least one evidence record before marking it complete.

| Module | Example evidence | Example score | Example observation |
| --- | --- | --- |
| Business & goals | Q3 planning brief; 850 enquiries per month baseline | 3/5 | Lead target is clear, but channel ownership is not documented. |
| Website & experience | Landing-page review; mobile test on 390px width | 2/5 | The primary enquiry form is below the first mobile screen. |
| SEO & discoverability | Search Console export; keyword-gap worksheet | 2/5 | Service pages do not target high-intent local search terms. |
| Measurement & data | GA4 property review; form-event screenshot | 2/5 | Form submissions are not recorded as conversions in GA4. |
| Paid media | Google Ads account read-only review | 3/5 | Branded campaigns perform well; non-brand search has no negative-keyword list. |

For each row, open **Assessment**, select the module, then enter an evidence label, source link or attachment, notes, score, and observation. Select **Save**, then **Mark complete** for every required module.

### 3. Add sample findings

Create these two findings in **Findings**:

| Field | Finding 1 example | Finding 2 example |
| --- | --- | --- |
| Title | Promote the enquiry form on mobile | Configure GA4 form conversion tracking |
| Related module | Website & experience | Measurement & data |
| Priority | High | High |
| Evidence | Mobile test shows the form below the first screen | No `generate_lead` conversion in GA4 |
| Impact | Prospective clients may abandon before reaching the form | Channel performance cannot be compared reliably |
| Recommended action | Add a visible "Book a consultation" button that anchors to the form | Create and test a GA4 conversion for successful form submission |
| Suggested owner | Web team | Analytics lead |
| Due date | 30 days from audit approval | 14 days from audit approval |

### 4. Create a roadmap

In **Roadmap**, add these implementation phases:

1. **0-30 days:** Move the mobile call-to-action above the fold and implement GA4 conversion tracking.
2. **31-60 days:** Optimise the three highest-value service pages for local search intent.
3. **61-90 days:** Add a negative-keyword process and review paid-search lead quality weekly.

Add the KPI target **"Qualified enquiry conversion rate: 3.2% to 4.0%"** and assign the web and analytics owners.

## Complete the audit workflow

Work through the ten stages shown at the top of the audit workspace:

1. **Client profile** — capture the client context and success goal.
2. **Audit team** — add the lead and subject-matter specialists.
3. **Client assets** — record websites, analytics access, source documents, and other assets.
4. **Assessment** — add module evidence, scores, observations, and benchmarks.
5. **Findings** — record client-ready findings with evidence, impact, and a recommended action.
6. **Roadmap** — set implementation phases and KPI targets.
7. **Quality review** — submit the completed audit and track reviewer feedback.
8. **Final report** — prepare the approved report output.
9. **Client delivery** — schedule delivery, download the PDF, or create a secure client link.
10. **Follow-up** — record a consultation, select an implementation package, and mark a deal won when appropriate.

### Add evidence and complete a module

1. Open the relevant module in **Assessment**.
2. Add an evidence label, notes, source URL, or approved attachment.
3. Save the evidence.
4. Add an assessment score and notes when applicable.
5. Mark the module **Complete**.

Required modules cannot be completed without evidence. An audit cannot be submitted until all required modules are complete.

### Add a finding

1. Open **Findings**.
2. Select the related module.
3. Enter a clear title, the problem, why it matters, evidence, impact, and recommended action.
4. Set priority, suggested owner, and due date where applicable.
5. Save the finding.

## Submit and approve with separate accounts

### Owner workflow

1. Sign in as the audit owner.
2. Confirm every required module is complete and supported by evidence.
3. Open **Quality review** and select **Submit for quality review**.
4. The status changes to **In review** and editing is locked.

### Reviewer workflow

1. Sign out and sign in as the staff reviewer or `superadmin@gmail.com` after it has been created.
2. Open **Quality review** from the navigation or the audit workspace.
3. Open the submitted audit.
4. Review completeness, technical accuracy, client relevance, clarity and tone, design and evidence, and methodology.
5. Either:
   - Select **Request changes**, enter actionable feedback, and return it to the owner; or
   - Complete every quality check and select **Approve audit**.
6. Approval changes the status to **Approved** and records the reviewer and timestamp.

## Deliver an approved audit

1. Open an **Approved** audit as its owner.
2. Select **Download report** for the professional PDF.
3. Select **Share audit** or **Open client link** to create or reopen the client view.
4. Select **Copy link** to send the link through your normal client communication channel.
5. Select **Preview** to review the client-facing page.
6. Select **Revoke access** whenever the link should stop working.

Only approved audits can be shared. Revoking a link immediately makes the public link unavailable.

## Deployment checklist

Set these backend environment values before deployment:

```env
DJANGO_SECRET_KEY=<a unique, long random value>
DJANGO_DEBUG=false
DJANGO_ALLOWED_HOSTS=auditpulseapi.vestaging.in
CORS_ALLOWED_ORIGINS=https://auditpulse.vestaging.in
DJANGO_CSRF_TRUSTED_ORIGINS=https://auditpulseapi.vestaging.in
DJANGO_DATABASE_PATH=/app/data/db.sqlite3
DJANGO_MEDIA_ROOT=/app/data/media
```

Do not commit `backend/.env`. Use `backend/.env.example` as the safe reference template.

## Verified checks

- Backend automated workflow tests: 21 passed
- Frontend production build: passed
- Live API health endpoint: available
- Live browser CORS preflight: allows the AuditPulse frontend
- Live regular-user login: verified

The live staff-review test remains pending until a real staff or superadmin account exists on the deployed backend.

## Final handoff checklist

Before sharing AuditPulse with a client team, confirm all of the following:

1. The audit owner can sign in and create the Northwest Living mock audit.
2. A separate staff reviewer or superadmin account has been created and can access **Quality review**.
3. Each required module includes evidence and can be marked complete.
4. The reviewer can request changes and approve a fully completed audit.
5. The approved audit downloads as a PDF and its client link opens in a private browser window.
6. The client link can be revoked and becomes unavailable immediately.
