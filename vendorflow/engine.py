"""Deterministic, explainable internal onboarding policy. No external verification."""
import re
import unicodedata
from datetime import date

POLICY_VERSION = '2026.1'
STAGES = [('intake', 'Submission intake'), ('documents', 'Read supporting evidence'),
          ('identity', 'Company & tax checks'), ('banking', 'Payment safeguards'),
          ('compliance', 'Compliance & duplicate checks'), ('decision', 'Decision & follow-up')]


def normalized(value):
    value = unicodedata.normalize('NFKD', str(value)).encode('ascii', 'ignore').decode()
    return re.sub(r'[^a-z0-9]', '', value.lower())


def name_matches(left, right):
    # Ignore punctuation, spacing and common legal suffixes, never fuzzy-match names.
    def key(name):
        words = re.findall(r'[a-z0-9]+', str(name).lower().replace('&', ' and '))
        return ''.join(w for w in words if w not in {'inc', 'incorporated', 'llc', 'ltd', 'limited', 'corporation', 'corp'})
    return bool(key(left)) and key(left) == key(right)


def valid_aba(value):
    return bool(re.fullmatch(r'\d{9}', value)) and value != '000000000' and int(value[:2]) in {*range(1, 13), *range(21, 33), *range(61, 73), 80} and sum(int(n) * [3, 7, 1][i % 3] for i, n in enumerate(value)) % 10 == 0


def valid_iban(value):
    value = re.sub(r'\s', '', value).upper()
    if not re.fullmatch(r'GB\d{2}[A-Z]{4}\d{14}', value):
        return False
    moved = value[4:] + value[:4]
    return int(''.join(str(ord(c) - 55) if c.isalpha() else c for c in moved)) % 97 == 1


def bank_key(payload):
    bank = payload.get('bank', {})
    if payload.get('country') == 'US':
        return 'US:' + normalized(bank.get('routing_number', '')) + ':' + normalized(bank.get('account_number', ''))
    return 'GB:' + normalized(bank.get('iban', ''))


def evaluate(payload, documents, prior, emit, today=None):
    today = today or date.today()
    findings = []
    def check(stage, code, ok, severity, good, bad, action=''):
        item = dict(stage=stage, code=code, outcome='pass' if ok else severity,
                    message=good if ok else bad, action='' if ok else action)
        findings.append(item)
        return ok
    def stage_done(stage):
        emit(stage, [f for f in findings if f['stage'] == stage])
    company = str(payload.get('legal_name', '')).strip()
    country = payload.get('country', '')
    email = str(payload.get('email', '')).strip()
    tax = re.sub(r'[\s-]', '', str(payload.get('tax_id', ''))).upper()
    bank = payload.get('bank', {})
    check('intake', 'required_fields', bool(company and country and tax and email and payload.get('address')), 'pending',
          'Required company and contact fields are present.', 'Required company or contact details are missing.', 'Provide legal name, country, tax ID, business address and contact email.')
    check('intake', 'email', bool(re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)), 'pending',
          'Contact email has a valid format.', 'Contact email format is invalid.', 'Provide a valid contact email.')
    supported = check('intake', 'country', country in {'US', 'GB'}, 'pending',
          'Country is supported by this policy.', 'Country requires a specialist review; automated approval is unavailable.', 'Ask procurement to review local tax and payment requirements.')
    stage_done('intake')
    evidence = {}
    for kind, label in [('tax', 'Tax registration'), ('bank', 'Bank ownership'), ('compliance', 'Compliance declaration')]:
        doc = documents.get(kind)
        exists = bool(doc and doc.get('text', '').strip())
        check('documents', kind + '_document', exists, 'pending', f'{label} evidence was read.',
              f'{label} evidence is missing or unreadable.', f'Upload readable {label.lower()} evidence as PDF or text.')
        if exists:
            fields = {}
            for line in doc['text'].splitlines():
                if ':' in line:
                    key, value = line.split(':', 1)
                    fields[key.strip().lower()] = value.strip()
            evidence[kind] = fields
            check('documents', kind + '_type', fields.get('document type', '').lower() == kind, 'pending',
                  f'{label} document type matches its upload slot.', f'{label} evidence has the wrong or unspecified document type.',
                  f'Provide the correct {label.lower()} document. See the evidence template for required labeled fields.')
    stage_done('documents')
    if supported:
        tax_valid = bool(re.fullmatch(r'\d{9}', tax)) and tax != '000000000' if country == 'US' else bool(re.fullmatch(r'\d{10}', tax)) and tax != '0000000000'
        check('identity', 'tax_format', tax_valid, 'pending', 'Tax ID format matches country policy (US EIN / UK UTR).',
              'Tax ID format does not match the selected country.', 'Correct the tax ID or country; US EIN needs 9 digits and UK UTR needs 10 digits.')
    if 'tax' in evidence:
        fields = evidence['tax']
        check('identity', 'tax_name', name_matches(company, fields.get('legal name', '')), 'pending',
              'Tax evidence legal name matches the submission.', 'Tax evidence legal name does not match the submission.', 'Provide tax evidence in the submitted legal entity name or correct the submission.')
        check('identity', 'tax_identity', normalized(tax) == normalized(fields.get('tax id', '')) and bool(tax), 'rejected' if tax and fields.get('tax id') else 'pending',
              'Tax ID agrees with the supporting evidence.', 'Submitted tax ID conflicts with supporting evidence.' if tax and fields.get('tax id') else 'Tax ID is missing from the submission or evidence.', 'Resolve the conflicting tax identities and submit corrected evidence for a new review.')
        check('identity', 'tax_country', fields.get('country', '').upper() == country, 'pending',
              'Tax evidence country agrees with the submission.', 'Tax evidence country differs or is missing.', 'Provide tax evidence for the declared country.')
    stage_done('identity')
    owner = str(bank.get('account_holder', ''))
    check('banking', 'owner_name', name_matches(company, owner), 'pending',
          'Bank account holder matches the legal entity.', 'Bank account holder differs from the vendor legal entity.', 'Provide a bank ownership letter for the legal entity; third-party payment needs manual review.')
    if supported:
        valid = valid_aba(str(bank.get('routing_number', ''))) and bool(re.fullmatch(r'\d{4,17}', str(bank.get('account_number', '')))) if country == 'US' else valid_iban(str(bank.get('iban', '')))
        check('banking', 'bank_format', valid, 'pending', 'Payment details pass country-specific structural and checksum checks.',
              'Payment details fail the country-specific format or checksum checks.', 'Correct the US routing/account numbers or UK IBAN using a bank-issued statement.')
    if 'bank' in evidence:
        fields = evidence['bank']
        match = name_matches(owner, fields.get('account holder', ''))
        if country == 'US':
            match = match and normalized(bank.get('account_number', '')) == normalized(fields.get('account number', '')) and normalized(bank.get('routing_number', '')) == normalized(fields.get('routing number', ''))
        elif country == 'GB':
            match = match and normalized(bank.get('iban', '')) == normalized(fields.get('iban', ''))
        required_bank_fields = [('account_holder', 'account holder')] + ([('routing_number', 'routing number'), ('account_number', 'account number')] if country == 'US' else [('iban', 'iban')])
        complete_bank = all(bank.get(key) and fields.get(label) for key, label in required_bank_fields)
        check('banking', 'bank_evidence', match, 'rejected' if complete_bank else 'pending', 'Payment details agree with bank ownership evidence.',
              'Payment details conflict with bank ownership evidence.' if complete_bank else 'Payment details are incomplete in the submission or bank ownership evidence.', 'Resolve conflicting bank ownership or account details before resubmitting. Procurement must verify changes independently.')
    stage_done('banking')
    if 'compliance' in evidence:
        fields = evidence['compliance']
        check('compliance', 'compliance_name', name_matches(company, fields.get('legal name', '')), 'pending',
              'Compliance declaration names this vendor.', 'Compliance declaration names a different entity or omits its name.', 'Submit a declaration naming this legal entity.')
        check('compliance', 'signed', bool(fields.get('signed by', '').strip()), 'pending',
              'Compliance declaration includes a signatory.', 'Compliance declaration is unsigned.', 'Provide the name of the authorized signatory in the declaration.')
        try:
            expiry = date.fromisoformat(fields.get('valid until', ''))
            current = expiry >= today
        except ValueError:
            current = False
        check('compliance', 'expiry', current, 'pending', 'Compliance declaration is within its stated validity period.',
              'Compliance declaration is expired or lacks a valid expiry date.', 'Upload a current declaration with Valid until in YYYY-MM-DD format.')
        declared = fields.get('sanctions declared', '').lower()
        check('compliance', 'sanctions', declared == 'no', 'rejected' if declared == 'yes' else 'pending',
              'Vendor declares no sanctions exposure (not an external screening).', 'Sanctions exposure declared.' if declared == 'yes' else 'Sanctions declaration is missing or unclear.',
              'Escalate declared sanctions exposure to compliance.' if declared == 'yes' else 'Complete the sanctions declaration with yes or no.')
    for existing in prior:
        if existing.get('status') != 'approved':
            continue
        old = existing['payload']
        same_entity = country == old.get('country') and normalized(tax) == normalized(old.get('tax_id', '')) and bool(tax)
        same_bank = bank_key(payload) == bank_key(old)
        if same_entity:
            check('compliance', 'duplicate_entity', False, 'pending', '', 'This tax identity already has an approved onboarding.', 'Use the existing vendor record or request a controlled amendment; do not create a duplicate.')
        elif same_bank:
            check('compliance', 'shared_bank', False, 'rejected', '', 'This bank account is already linked to a different approved tax identity.', 'Procurement must investigate the shared account before a new submission.')
    stage_done('compliance')
    status = 'rejected' if any(f['outcome'] == 'rejected' for f in findings) else 'pending' if any(f['outcome'] == 'pending' for f in findings) else 'approved'
    issues = [f for f in findings if f['outcome'] != 'pass']
    actions = list(dict.fromkeys(f['action'] for f in issues if f['action']))
    subject = f'Vendor onboarding {status}: {company or "Unnamed vendor"}'
    body = f'Hello {company or "vendor"},\n\n'
    if status == 'approved':
        body += 'Your submission passed our internal onboarding policy. Procurement can proceed to its payment-release controls.\n'
    else:
        body += f'Your onboarding status is {status}. Please address the following:\n\n' + '\n'.join(f'{i + 1}. {action}' for i, action in enumerate(actions)) + '\n'
    body += '\nThese checks assess submitted evidence and internal consistency; they do not authenticate documents, verify account ownership with a bank, or screen external sanctions lists.\n\nProcurement Operations'
    result = dict(status=status, findings=findings, actions=actions, policy_version=POLICY_VERSION,
                  communication=dict(to=email, subject=subject, body=body, delivery='in_app', note='Available in the result and downloadable follow-up; no external email sent.'))
    emit('decision', [dict(outcome=status, message=f'Internal policy decision: {status}. Follow-up created in the submission result.')])
    return result
