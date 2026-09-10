"""Offline invariants of the container validation API; no cloud credentials."""
from pathlib import Path
import re
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA = re.compile(r'[^@]+@[0-9a-f]{40}')
EXPRESSION = re.compile(r'\$\{\{.*?\}\}', re.DOTALL)
REUSABLES = {'validate-apko-images.yml', 'test-runtime-images.yml'}


def steps_errors(steps, composite=False):
    errors = []
    for step in steps:
        uses = step.get('uses', '')
        if uses and not uses.startswith('./') and not SHA.fullmatch(uses):
            errors.append('external dependency must use a full SHA')
        if uses.startswith('actions/checkout@'):
            options = step.get('with') or {}
            if options.get('persist-credentials') is not False:
                errors.append('checkout must not persist credentials')
            if 'repository' in options or 'ref' in options:
                errors.append('validation must check out the caller at the event commit')
        if EXPRESSION.search(step.get('run', '')):
            errors.append('pass expressions through env, never inside run')
        if composite and 'run' in step and not step.get('shell'):
            errors.append('composite run requires an explicit shell')
        if uses.startswith('actions/upload-artifact@'):
            options = step.get('with') or {}
            if not isinstance(options.get('retention-days'), int):
                errors.append('artifact retention must be explicit')
            if str(options.get('name', '')).startswith('validated-oci-'):
                if step.get('if') or step.get('continue-on-error'):
                    errors.append('validated OCI upload must require success')
                if options.get('if-no-files-found') != 'error':
                    errors.append('validated OCI upload must reject missing files')
    return errors


def workflow_errors(document, reusable=False):
    errors = []
    events = document.get('on', document.get(True)) or {}
    if 'permissions' not in document:
        errors.append('workflow permissions must be explicit')
    if reusable and set(events) != {'workflow_call'}:
        errors.append('shared executors accept workflow_call only')
    if reusable and document.get('permissions') not in (
            {'contents': 'read'}, {'contents': 'read', 'actions': 'read'}):
        errors.append('shared validation must have read-only permissions without OIDC')
    if reusable and (events.get('workflow_call') or {}).get('secrets'):
        errors.append('shared validation must not request secrets')
    for job in document.get('jobs', {}).values():
        if job.get('secrets') or job.get('environment'):
            errors.append('validation must not inherit secrets or deployment environments')
        if job.get('permissions') and any(v != 'read' for v in job['permissions'].values()):
            errors.append('job permissions must stay read-only')
        if 'steps' in job:
            if not isinstance(job.get('timeout-minutes'), int):
                errors.append('executor timeout must be explicit')
            errors += steps_errors(job['steps'])
        elif job.get('uses') and not job['uses'].startswith('./') and not SHA.fullmatch(job['uses']):
            errors.append('called workflow must use a full SHA')
        if 'matrix' in job.get('strategy', {}) and job['strategy'].get('fail-fast') is not False:
            errors.append('matrix failures must remain isolated')
    return errors


def check(root=ROOT):
    errors = []
    for path in sorted((root / '.github/workflows').glob('*.yml')):
        errors += [f'{path.name}: {error}' for error in workflow_errors(
            yaml.safe_load(path.read_text()), path.name in REUSABLES)]
    action = yaml.safe_load((root / 'actions/setup-trivy/action.yml').read_text())
    if action.get('inputs'):
        errors.append('Trivy setup must not accept commands or tool version overrides')
    errors += steps_errors(action['runs']['steps'], composite=True)
    return errors


if __name__ == '__main__':
    problems = check()
    for problem in problems:
        print(f'::error::{problem}', file=sys.stderr)
    raise SystemExit(bool(problems))
