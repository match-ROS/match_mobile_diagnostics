"""Configuration changes preserve unrelated Chrony settings and are idempotent."""
import importlib.util
from pathlib import Path

import pytest

script = Path(__file__).resolve().parents[1] / 'scripts/configure_chrony.py'
spec = importlib.util.spec_from_file_location('configure_chrony', script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_default_pools_replaced_without_touching_other_settings():
    original = 'confdir /etc/chrony/conf.d\npool ntp.ubuntu.com iburst maxsources 4\n' \
               'pool 0.ubuntu.pool.ntp.org iburst maxsources 1\nrtcsync\n'
    updated = module.updated_config(original)
    assert 'rtcsync\n' in updated
    assert not any(line.startswith('pool ') for line in updated.splitlines())
    assert module.updated_config(updated) == updated


def test_unexpected_custom_source_requires_manual_review():
    with pytest.raises(ValueError, match='Weitere aktive'):
        module.updated_config('confdir /etc/chrony/conf.d\nserver other.example.net iburst\n')
    with pytest.raises(ValueError, match='conf.d'):
        module.updated_config('pool ntp.ubuntu.com iburst\n')
