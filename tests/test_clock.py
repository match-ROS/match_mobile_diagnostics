"""Chrony diagnostics use simulated outputs and bounded clock samples."""
from pathlib import Path
from unittest.mock import patch

from match_mobile_diagnostics import clock


TRACKING_OK = """Reference ID    : 0A910832 (10.145.8.50)
System time     : 0.002000000 seconds fast of NTP time
Leap status     : Normal
"""
SOURCES_OK = "^* 10.145.8.50             2   6   377    24    +2ms[+2ms] +/- 10ms\n"


def by_id(checks, suffix):
    return next(item for item in checks if item['id'].endswith(suffix))


def test_config_includes_and_selected_chrony_server(tmp_path, monkeypatch):
    config = tmp_path / 'chrony.conf'
    config.write_text('confdir conf.d\n# pool incorrect.example.net\n')
    included = tmp_path / 'conf.d'
    included.mkdir()
    (included / 'server.conf').write_text('server 10.145.8.50 iburst\n')
    monkeypatch.setattr(clock.shutil, 'which', lambda _: '/usr/bin/chronyc')
    monkeypatch.setattr(clock, '_chronyc', lambda args: (TRACKING_OK if args == ['tracking'] else SOURCES_OK, None))
    checks = clock.collect_clock('10.145.8.50', config_path=config)
    assert [by_id(checks, suffix)['status'] for suffix in ('.config', '.available', '.source', '.sync')] == ['pass'] * 4
    assert by_id(checks, '.sync')['actual']['system_offset_seconds'] == 0.002
    assert clock.clock_stat(checks)['status'] == 'pass'


def test_wrong_source_is_separate_from_healthy_clock(tmp_path, monkeypatch):
    config = tmp_path / 'chrony.conf'
    config.write_text('server 10.145.8.51 iburst\n')
    monkeypatch.setattr(clock.shutil, 'which', lambda _: '/usr/bin/chronyc')
    monkeypatch.setattr(clock, '_chronyc', lambda args: (TRACKING_OK if args == ['tracking'] else
                        SOURCES_OK.replace('10.145.8.50', '10.145.8.51'), None))
    checks = clock.collect_clock('10.145.8.50', config_path=config)
    assert by_id(checks, '.config')['status'] == 'fail'
    assert by_id(checks, '.source')['status'] == 'fail'
    assert by_id(checks, '.sync')['status'] == 'pass'


def test_missing_chronyc_does_not_fabricate_runtime_state(tmp_path, monkeypatch):
    monkeypatch.setattr(clock.shutil, 'which', lambda _: None)
    checks = clock.collect_clock('10.145.8.50', config_path=tmp_path / 'missing.conf')
    assert by_id(checks, '.config')['status'] == 'unknown'
    assert by_id(checks, '.available')['status'] == 'fail'
    assert by_id(checks, '.source')['status'] == 'unknown'
    assert by_id(checks, '.sync')['status'] == 'unknown'


def test_unsynchronised_or_large_chrony_offset(tmp_path, monkeypatch):
    config = tmp_path / 'chrony.conf'
    config.write_text('server 10.145.8.50 iburst\n')
    monkeypatch.setattr(clock.shutil, 'which', lambda _: '/usr/bin/chronyc')
    monkeypatch.setattr(clock, '_chronyc', lambda args: (TRACKING_OK.replace('Normal', 'Not synchronised')
                        if args == ['tracking'] else SOURCES_OK.replace('^*', '^?'), None))
    checks = clock.collect_clock('10.145.8.50', config_path=config)
    assert by_id(checks, '.source')['status'] == 'fail'
    assert by_id(checks, '.sync')['status'] == 'fail'
    monkeypatch.setattr(clock, '_chronyc', lambda args: (TRACKING_OK.replace('0.002000000', '0.250000000')
                        if args == ['tracking'] else SOURCES_OK, None))
    assert by_id(clock.collect_clock('10.145.8.50', config_path=config), '.sync')['status'] == 'warn'


def test_ssh_clock_interval_never_forces_ambiguous_result(monkeypatch):
    def sample(remote_ns):
        wall = iter((1_000_000_000, 1_100_000_000))
        mono = iter((0, 100_000_000))
        monkeypatch.setattr(clock.time, 'time_ns', lambda: next(wall))
        monkeypatch.setattr(clock.time, 'monotonic_ns', lambda: next(mono))
        with patch.object(clock.processes, 'run', return_value=(0, str(remote_ns), '')):
            return clock.compare_remote_clock('mur620a')
    assert sample(1_050_000_000)['status'] == 'pass'
    assert sample(2_000_000_000)['status'] == 'fail'
    uncertain = sample(1_600_000_000)
    assert uncertain['status'] == 'unknown'
    assert uncertain['actual']['interval_ms'] == [500.0, 600.0]


def test_selected_but_old_chrony_sample_is_warning(tmp_path, monkeypatch):
    config = tmp_path / 'chrony.conf'
    config.write_text('server 10.145.8.50 iburst\n')
    monkeypatch.setattr(clock.shutil, 'which', lambda _: '/usr/bin/chronyc')
    monkeypatch.setattr(clock, '_chronyc', lambda args: (TRACKING_OK if args == ['tracking'] else
                        SOURCES_OK.replace('377    24', '377   12m'), None))
    checks = clock.collect_clock('10.145.8.50', config_path=config)
    assert by_id(checks, '.source')['status'] == 'warn'
    assert by_id(checks, '.source')['actual']['last_good_sample_age_seconds'] == 720
