"""Validated project configuration. No credentials or network access."""
import json
import math
import os
from pathlib import Path
import re
import tempfile

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNTIME = {
    'omp_executable': 'omp',
    'dashboard_port': 8765,
    'worker_timeout_seconds': 1200,
    'success_pause_seconds': 60,
    'idle_pause_seconds': 300,
    'service_name': 'jobapp',
    'credentials_file': '~/.omp/job-applications/credentials.json',
}
DEFAULT_CAMPAIGN = {
    'target_total': 50,
    'worker_model': None,
    'priority_job_ids': [],
    'max_submissions_per_run': None,
    'max_submissions_per_day': None,
    'max_submissions_per_host_per_day': None,
    'max_details_per_hour': None,
    'max_loads_per_day': None,
    'skip_unknown_questions': True,
    'skip_captcha': True,
    'skip_sensitive_identity_fields': True,
    'skip_separate_binding_legal_agreements': True,
    'standard_application_privacy_and_accuracy_attestation': False,
    'local_resume_only': True,
    'no_further_questions': True,
}
CAMPAIGN_KEYS = set(DEFAULT_CAMPAIGN) | {
    'runtime', 'baseline_confirmed', 'manual_recovery_requested_at',
    'manual_recovery_job_ids', 'skip_new_otp', 'willing_to_relocate',
    'relocation_geography', 'employment_types', 'notes',
}


class ConfigError(ValueError):
    pass


_PROFILE_FIELDS = {
    'candidate': {'name', 'first_name', 'last_name', 'email', 'phone', 'location', 'resume_path',
                  'work_authorization', 'requires_sponsorship', 'us_citizen', 'green_card_holder',
                  'has_ead', 'ead_type', 'street', 'city', 'state', 'zip', 'country',
                  'authorization_notes', 'linkedin_url', 'website'},
    'preferences': {'location_mode', 'locations', 'accept_remote', 'max_commute_miles',
                    'allow_senior_ic', 'employment_types', 'min_salary_usd',
                    'salary_expectation_usd', 'salary_range_rule', 'unknown_salary',
                    'max_travel_percent', 'willing_to_relocate', 'submit_mode', 'linkedin_geo_id'},
    'search': {'queries', 'title_include', 'title_exclude', 'skills', 'seniority_max_years_required',
               'core_title_words', 'role_words', 'context_words', 'domain_words', 'title_out_of_lane'},
    'application_permissions': {'account_creation', 'password_reset', 'google_sign_in',
                                'mailbox_verification'},
}


def _reject_constant(value):
    raise ValueError('non-finite JSON number')


def _validate_profile_values(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).lower() in {'password', 'passwd', 'pwd', 'token', 'api_key',
                                   'access_token', 'refresh_token', 'client_secret', 'private_key'}:
                raise ConfigError('Profile must not contain credentials; use the credential command')
            _validate_profile_values(item)
    elif isinstance(value, list):
        for item in value:
            _validate_profile_values(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ConfigError('Profile numbers must be finite')


def read_json(path):
    try:
        with Path(path).open() as handle:
            value = json.load(handle, parse_constant=_reject_constant)
    except (OSError, ValueError) as error:
        raise ConfigError(f'{path}: {error}') from None
    if not isinstance(value, dict):
        raise ConfigError(f'{path}: expected a JSON object')
    return value


def atomic_write_json(path, value):
    """Replace one configuration file without exposing a partial write."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, indent=2, allow_nan=False)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _integer(value, name, minimum=0, maximum=None):
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        extent = f'{minimum}..{maximum}' if maximum is not None else f'>= {minimum}'
        raise ConfigError(f'{name}: expected an integer {extent}')


def _strings(value, name, nonempty=False):
    if not isinstance(value, list) or (nonempty and not value) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ConfigError(f'{name}: expected a list of nonempty strings')


def validate_campaign(value):
    if not isinstance(value, dict):
        raise ConfigError('campaign: expected a JSON object')
    unknown = set(value) - CAMPAIGN_KEYS
    if unknown:
        raise ConfigError('campaign: unknown setting(s): ' + ', '.join(sorted(unknown)))
    result = {**DEFAULT_CAMPAIGN, **value}
    if result['target_total'] is not None:
        _integer(result['target_total'], 'campaign.target_total')
    if result['worker_model'] is not None and (not isinstance(result['worker_model'], str) or not result['worker_model'].strip()):
        raise ConfigError('campaign.worker_model: expected a nonempty model identifier or null')
    for key in DEFAULT_CAMPAIGN:
        if key.startswith('max_') and result[key] is not None:
            _integer(result[key], 'campaign.' + key)
        elif type(DEFAULT_CAMPAIGN[key]) is bool and type(result[key]) is not bool:
            raise ConfigError('campaign.' + key + ': expected true or false')
    for key in ('skip_unknown_questions', 'skip_captcha', 'skip_sensitive_identity_fields',
                'skip_separate_binding_legal_agreements', 'local_resume_only', 'no_further_questions'):
        if result[key] is not True:
            raise ConfigError('campaign.' + key + ': this safety policy cannot be disabled')
    for key in ('priority_job_ids', 'manual_recovery_job_ids', 'employment_types'):
        if key in result:
            _strings(result[key], 'campaign.' + key)
    for key in ('skip_new_otp', 'willing_to_relocate'):
        if key in result and type(result[key]) is not bool:
            raise ConfigError('campaign.' + key + ': expected true or false')
    if 'baseline_confirmed' in result:
        _integer(result['baseline_confirmed'], 'campaign.baseline_confirmed')
    if 'manual_recovery_requested_at' in result:
        stamp = result['manual_recovery_requested_at']
        if type(stamp) not in (int, float) or not math.isfinite(stamp) or stamp < 0:
            raise ConfigError('campaign.manual_recovery_requested_at: expected a finite nonnegative timestamp')
    runtime = result.get('runtime', {})
    if not isinstance(runtime, dict) or set(runtime) - set(DEFAULT_RUNTIME):
        raise ConfigError('campaign.runtime: expected known runtime settings only')
    runtime = {**DEFAULT_RUNTIME, **runtime}
    for key in ('omp_executable', 'credentials_file'):
        if not isinstance(runtime[key], str) or not runtime[key].strip() or '\x00' in runtime[key]:
            raise ConfigError('campaign.runtime.' + key + ': expected a nonempty path or executable')
    _integer(runtime['dashboard_port'], 'campaign.runtime.dashboard_port', 1, 65535)
    _integer(runtime['worker_timeout_seconds'], 'campaign.runtime.worker_timeout_seconds', 1)
    for key in ('success_pause_seconds', 'idle_pause_seconds'):
        _integer(runtime[key], 'campaign.runtime.' + key)
    if not isinstance(runtime['service_name'], str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,47}', runtime['service_name']):
        raise ConfigError('campaign.runtime.service_name: use a lowercase name containing letters, numbers, _ or -')
    result['runtime'] = runtime
    return result


def load_campaign(root=ROOT):
    return validate_campaign(read_json(Path(root) / 'data/campaign.json'))


def resolve_path(value, root=ROOT):
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path(root) / path


def _without_nulls(value):
    """A null means 'unknown, not answered yet'. Validation checks only what the user filled in."""
    if isinstance(value, dict):
        return {key: _without_nulls(item) for key, item in value.items() if item is not None}
    return value


def validate_profile(value, root=ROOT, ready=False):
    original = value
    if not isinstance(value, dict):
        raise ConfigError('profile: expected a JSON object')
    value = _without_nulls(value)
    unknown = set(value) - (set(_PROFILE_FIELDS) | {'answers', 'sources', 'application_instructions'})
    if unknown:
        raise ConfigError('profile: unknown setting(s): ' + ', '.join(sorted(unknown)))
    _validate_profile_values(value)
    for key in ('candidate', 'preferences', 'search', 'answers', 'application_permissions'):
        if key in value and not isinstance(value[key], dict):
            raise ConfigError('profile.' + key + ': expected an object')
    for section, fields in _PROFILE_FIELDS.items():
        unknown = set(value.get(section, {})) - fields
        if unknown:
            raise ConfigError('profile.' + section + ': unknown setting(s): ' + ', '.join(sorted(unknown)))
    candidate, prefs, search, answers = (value.get(key, {}) for key in ('candidate', 'preferences', 'search', 'answers'))
    if prefs.get('salary_range_rule', 'midpoint') != 'midpoint':
        raise ConfigError('profile.preferences.salary_range_rule: only midpoint is supported')
    if prefs.get('unknown_salary', 'allow') not in ('allow', 'reject'):
        raise ConfigError('profile.preferences.unknown_salary: expected allow or reject')
    if prefs.get('submit_mode', 'auto') != 'auto':
        raise ConfigError('profile.preferences.submit_mode: only auto is supported; setup/doctor/status never submit')
    if 'seniority_max_years_required' in search:
        _integer(search['seniority_max_years_required'], 'profile.search.seniority_max_years_required')
    if 'default_skill_years' in answers:
        _integer(answers['default_skill_years'], 'profile.answers.default_skill_years')
    if 'years_experience' in answers and not isinstance(answers['years_experience'], dict):
        raise ConfigError('profile.answers.years_experience: expected an object')
    for years in answers.get('years_experience', {}).values():
        if type(years) not in (int, float) or not math.isfinite(years) or years < 0:
            raise ConfigError('profile.answers.years_experience: expected finite nonnegative years')
    reviewed = answers.get('reviewed_questions', {})
    if not isinstance(reviewed, dict):
        raise ConfigError('profile.answers.reviewed_questions: expected an object of exact question to answer')
    for question, reviewed_answer in reviewed.items():
        ok = type(reviewed_answer) is bool or (isinstance(reviewed_answer, str) and reviewed_answer.strip()) or \
            (type(reviewed_answer) in (int, float) and math.isfinite(reviewed_answer))
        if not question.strip() or not ok:
            raise ConfigError('profile.answers.reviewed_questions: each entry is a nonempty question and a yes/no, text or number answer')
    for key in ('min_salary_usd', 'salary_expectation_usd', 'max_commute_miles', 'max_travel_percent'):
        if key in prefs:
            _integer(prefs[key], 'profile.preferences.' + key)
    if prefs.get('max_travel_percent', 0) > 100:
        raise ConfigError('profile.preferences.max_travel_percent: must not exceed 100')
    if 'salary_expectation_usd' in answers:
        _integer(answers['salary_expectation_usd'], 'profile.answers.salary_expectation_usd')
        if 'salary_expectation_usd' in prefs and answers['salary_expectation_usd'] != prefs['salary_expectation_usd']:
            raise ConfigError('profile: preferences and answers salary_expectation_usd must agree')
    for key in ('accept_remote', 'allow_senior_ic', 'willing_to_relocate'):
        if key in prefs and type(prefs[key]) is not bool:
            raise ConfigError('profile.preferences.' + key + ': expected true or false')
    for key in ('requires_sponsorship', 'us_citizen', 'green_card_holder', 'has_ead'):
        if key in candidate and type(candidate[key]) is not bool:
            raise ConfigError('profile.candidate.' + key + ': expected true or false')
    for item in candidate.values():
        if isinstance(item, str) and '\x00' in item:
            raise ConfigError('profile.candidate: strings must not contain NUL characters')
    for key in ('queries', 'title_include', 'title_exclude', 'skills',
                'core_title_words', 'role_words', 'context_words', 'domain_words', 'title_out_of_lane'):
        if key in search:
            _strings(search[key], 'profile.search.' + key, nonempty=ready and key == 'queries')
    if 'linkedin_geo_id' in prefs and not re.fullmatch(r'\d{6,12}', str(prefs['linkedin_geo_id'])):
        raise ConfigError('profile.preferences.linkedin_geo_id: expected the digits of a LinkedIn region id')
    if 'locations' in prefs:
        _strings(prefs['locations'], 'profile.preferences.locations')
    if 'employment_types' in prefs:
        _strings(prefs['employment_types'], 'profile.preferences.employment_types')
    if 'application_instructions' in value:
        _strings(value['application_instructions'], 'profile.application_instructions')
    for key, permission in value.get('application_permissions', {}).items():
        if type(permission) is not bool:
            raise ConfigError('profile.application_permissions.' + key + ': expected true or false')
    if ready:
        for key in ('name', 'first_name', 'last_name', 'email', 'phone', 'country',
                    'location', 'work_authorization', 'resume_path'):
            if not isinstance(candidate.get(key), str) or not candidate[key].strip():
                raise ConfigError('profile.candidate.' + key + ': required before starting')
        if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', candidate['email']):
            raise ConfigError('profile.candidate.email: expected an email address')
        if type(candidate.get('requires_sponsorship')) is not bool:
            raise ConfigError('profile.candidate.requires_sponsorship: explicitly set true or false before starting')
        _strings(search.get('queries'), 'profile.search.queries', nonempty=True)
        _integer(prefs.get('min_salary_usd'), 'profile.preferences.min_salary_usd')
        resume = resolve_path(candidate['resume_path'], root)
        try:
            with resume.open('rb') as handle:
                if handle.read(5) != b'%PDF-':
                    raise ConfigError('profile.candidate.resume_path: expected a local PDF resume')
        except OSError:
            raise ConfigError('profile.candidate.resume_path: local resume is missing or unreadable') from None
    return original


def load_profile(root=ROOT, ready=False):
    return validate_profile(read_json(Path(root) / 'profile.json'), root, ready=ready)


def default_profile():
    """An intentionally incomplete identity; starting requires reviewed real details."""
    return {
        'candidate': {'name': '', 'first_name': '', 'last_name': '', 'email': '', 'phone': '',
                      'location': '', 'country': '', 'work_authorization': '', 'resume_path': 'data/resume.pdf'},
        'preferences': {'locations': [], 'accept_remote': True, 'min_salary_usd': 75000,
                        'salary_expectation_usd': 90000, 'salary_range_rule': 'midpoint',
                        'unknown_salary': 'allow', 'employment_types': ['permanent_full_time'],
                        'max_commute_miles': 60, 'max_travel_percent': 10},
        'search': {'queries': [], 'title_include': ['analyst'], 'title_exclude': [],
                   'skills': [], 'seniority_max_years_required': 8},
        'answers': {'salary_expectation_usd': 90000},
        'application_permissions': {'account_creation': False, 'password_reset': False,
                                    'google_sign_in': False, 'mailbox_verification': False},
        'application_instructions': [],
    }
